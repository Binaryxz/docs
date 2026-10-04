"""Módulo 4 (primeiro provedor) — Validação de WhatsApp via CheckNumber.AI.

Por que esse provedor e não outra coisa: já tentamos a rota grátis (Baileys/
WhatsApp Web não-oficial, conectando um número real via QR code) num projeto
anterior — funcionou, mas teve um incidente real (loop de reconexão sem
limite rodou ~12h sem parar e o WhatsApp bloqueou o número usado). Depois de
pesquisar, a conclusão foi: "grátis + sem conectar número + em escala" não
existe de verdade no mercado. CheckNumber.AI foi a opção calibrada e
confirmada funcionando (contra dois números de referência conhecidos — um
com WhatsApp, um sem) nesse projeto anterior.

Diferença importante: a checagem roda 100% na infraestrutura deles — nunca
conectamos nenhum número nosso, sem QR code, sem sessão pra manter viva.
Custo real observado: ~$0,0225 por número (confirme /v1/balance antes de
rodar lote grande — é saldo em dólar, não "créditos").

API (confirmada por teste real): base https://api.checknumber.ai, header
"X-API-Key", tarefa assíncrona baseada em arquivo — POST /v1/tasks (um .txt
com números em E.164, um por linha) devolve task_id; POST /v1/gettasks com
esse task_id até status="exported"; o resultado final tem "result_url", um
link assinado pra um .zip com "all.csv" (colunas number,activated).

Cache: resultado fica em `cache_enriquecimento` (chave = cnpj), por
`dias_cache_enriquecimento` — não paga duas vezes pelo mesmo número a não
ser que o telefone da empresa tenha mudado ou o cache tenha expirado.
"""
import csv
import io
import json
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from app.config import decifrar, get_config
from app.db import execute, fetch_one, query_df

BASE_URL = "https://api.checknumber.ai"
SEGUNDOS_ENTRE_POLLS = 5
TIMEOUT_POLL_SEGUNDOS = 300
CUSTO_POR_NUMERO_USD = 0.0225  # observado; confira /v1/balance pra um valor atual


@dataclass
class ResultadoValidacaoWhatsapp:
    total_enviados: int = 0
    com_whatsapp: int = 0
    sem_whatsapp: int = 0
    custo_real_usd: float = 0.0
    ignorados_cache: int = 0
    ignorados_sem_telefone: int = 0
    erros: list[str] = field(default_factory=list)


def normaliza_e164(numero_bruto: str) -> str:
    digitos = re.sub(r"\D", "", numero_bruto or "")
    if not digitos:
        return ""
    if not digitos.startswith("55") and len(digitos) in (10, 11):
        digitos = "55" + digitos
    return "+" + digitos


def _chave_api() -> str:
    valor = get_config("chave_api_checknumber")
    return decifrar(valor) if valor else ""


def consultar_saldo() -> float:
    chave = _chave_api()
    resp = requests.get(f"{BASE_URL}/v1/balance", headers={"X-API-Key": chave}, timeout=20)
    resp.raise_for_status()
    return resp.json().get("balance", 0)


def _submeter_task(numeros: list[str], chave: str) -> dict:
    conteudo = "\n".join(numeros).encode("utf-8")
    arquivos = {"file": ("numeros.txt", conteudo, "text/plain")}
    dados = {"task_type": "ws"}
    resp = requests.post(f"{BASE_URL}/v1/tasks", headers={"X-API-Key": chave}, files=arquivos, data=dados, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _aguardar_resultado(task_id: str, chave: str) -> dict:
    inicio = time.time()
    while time.time() - inicio < TIMEOUT_POLL_SEGUNDOS:
        resp = requests.post(f"{BASE_URL}/v1/gettasks", headers={"X-API-Key": chave}, data={"task_id": task_id}, timeout=20)
        resp.raise_for_status()
        corpo = resp.json()
        if corpo.get("status") == "exported":
            return corpo
        time.sleep(SEGUNDOS_ENTRE_POLLS)
    raise TimeoutError(f"Task {task_id} do CheckNumber.AI não terminou em {TIMEOUT_POLL_SEGUNDOS}s")


def _baixar_resultados(result_url: str) -> dict[str, str]:
    """{numero_sem_mais: 'yes'|'no'}"""
    resp = requests.get(result_url, timeout=60)
    resp.raise_for_status()
    resultados = {}
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        nome_csv = next(n for n in z.namelist() if n.endswith("all.csv"))
        with z.open(nome_csv) as f:
            texto = io.TextIOWrapper(f, encoding="utf-8")
            for linha in csv.DictReader(texto):
                resultados[linha["number"].lstrip("+")] = linha["activated"]
    return resultados


def _cache_valido(cnpj: str, dias_cache: int) -> dict | None:
    row = fetch_one(
        "SELECT resultado_json, atualizado_em FROM cache_enriquecimento "
        "WHERE cnpj = :c AND provedor = 'checknumber'",
        {"c": cnpj},
    )
    if not row:
        return None
    if row["atualizado_em"] < datetime.now(timezone.utc) - timedelta(days=dias_cache):
        return None
    return row["resultado_json"]


def _gravar_cache(cnpj: str, resultado: dict) -> None:
    execute(
        """
        INSERT INTO cache_enriquecimento (cnpj, provedor, resultado_json, atualizado_em)
        VALUES (:cnpj, 'checknumber', CAST(:resultado AS JSONB), now())
        ON CONFLICT (cnpj) DO UPDATE SET
            provedor = 'checknumber', resultado_json = CAST(:resultado AS JSONB), atualizado_em = now()
        """,
        {"cnpj": cnpj, "resultado": json.dumps(resultado)},
    )


def _registrar_gasto(cnpj: str, custo: float) -> None:
    execute(
        "INSERT INTO gasto_enriquecimento (provedor, cnpj, custo) VALUES ('checknumber', :cnpj, :custo)",
        {"cnpj": cnpj, "custo": custo},
    )


def _gasto_do_periodo(desde: datetime) -> float:
    row = fetch_one(
        "SELECT COALESCE(SUM(custo), 0) AS total FROM gasto_enriquecimento "
        "WHERE provedor = 'checknumber' AND criado_em >= :desde",
        {"desde": desde},
    )
    return float(row["total"])


def validar_whatsapp_leads_pendentes(limite: int | None = None) -> ResultadoValidacaoWhatsapp:
    """Valida WhatsApp dos leads qualificados ('em_analise') com telefone,
    respeitando cache, score mínimo e tetos de gasto diário/mensal. É a
    rotina chamada pelo botão manual e, opcionalmente, pelo agendador."""
    resultado = ResultadoValidacaoWhatsapp()

    chave = _chave_api()
    if not chave:
        resultado.erros.append("CHECKNUMBER_API_KEY não configurada (tela de Configurações).")
        return resultado

    score_minimo = get_config("score_minimo_para_enriquecer")
    dias_cache = get_config("dias_cache_enriquecimento")
    teto_diario = get_config("teto_gasto_diario")
    teto_mensal = get_config("teto_gasto_mensal")

    agora = datetime.now(timezone.utc)
    gasto_hoje = _gasto_do_periodo(agora - timedelta(days=1))
    gasto_mes = _gasto_do_periodo(agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0))

    candidatos = query_df(
        """
        SELECT DISTINCT er.cnpj, er.telefone
        FROM leads l
        JOIN empresas_receita er ON er.cnpj = l.cnpj
        WHERE l.status = 'em_analise'
          AND l.score_lead >= :score_minimo
          AND er.telefone IS NOT NULL AND er.telefone <> ''
        """,
        {"score_minimo": score_minimo},
    )
    if limite:
        candidatos = candidatos.head(limite)

    numero_por_cnpj: dict[str, str] = {}
    for _, row in candidatos.iterrows():
        cache = _cache_valido(row["cnpj"], dias_cache)
        if cache is not None:
            resultado.ignorados_cache += 1
            continue
        numero = normaliza_e164(row["telefone"])
        if not numero:
            resultado.ignorados_sem_telefone += 1
            continue
        numero_por_cnpj[row["cnpj"]] = numero

    if not numero_por_cnpj:
        return resultado

    custo_previsto = len(numero_por_cnpj) * CUSTO_POR_NUMERO_USD
    if gasto_hoje + custo_previsto > teto_diario:
        resultado.erros.append(
            f"Teto de gasto diário (R${teto_diario}) seria excedido (gasto hoje: {gasto_hoje:.2f} "
            f"+ previsto: {custo_previsto:.2f}) — validação pausada, nada foi enviado."
        )
        return resultado
    if gasto_mes + custo_previsto > teto_mensal:
        resultado.erros.append(
            f"Teto de gasto mensal (R${teto_mensal}) seria excedido — validação pausada, nada foi enviado."
        )
        return resultado

    numeros_unicos = sorted(set(numero_por_cnpj.values()))
    try:
        task = _submeter_task(numeros_unicos, chave)
        task_final = _aguardar_resultado(task["task_id"], chave)
        ativados = _baixar_resultados(task_final["result_url"])
    except (requests.RequestException, TimeoutError) as exc:
        resultado.erros.append(f"Falha consultando CheckNumber.AI: {exc}")
        return resultado

    # "actual_amount" vem como {"amount": "0.045000", "currency": "USD"} na API
    # real (não um número simples, como a doc/script de referência sugeria) —
    # confirmado rodando contra a API de verdade.
    valor_cobrado = (task_final.get("actual_amount") or {}).get("amount")
    custo_real_total = float(valor_cobrado) if valor_cobrado is not None else custo_previsto
    custo_por_numero = custo_real_total / len(numeros_unicos) if numeros_unicos else 0

    for cnpj, numero in numero_por_cnpj.items():
        resposta = ativados.get(numero.lstrip("+"))
        tem_whatsapp = resposta == "yes"
        _gravar_cache(cnpj, {"tem_whatsapp": tem_whatsapp, "telefone_validado": numero})
        _registrar_gasto(cnpj, custo_por_numero)
        resultado.total_enviados += 1
        resultado.custo_real_usd += custo_por_numero
        if tem_whatsapp:
            resultado.com_whatsapp += 1
        else:
            resultado.sem_whatsapp += 1

    return resultado


def calibrar(numero_com_whatsapp_conhecido: str, numero_sem_whatsapp_conhecido: str) -> dict:
    """Checklist do provedor: nunca confiar numa API nova sem confirmar
    contra pelo menos 2 números cuja resposta certa já se sabe. Não usa
    cache nem grava gasto fora do fluxo normal — é só uma checagem pontual."""
    chave = _chave_api()
    nums = [normaliza_e164(numero_com_whatsapp_conhecido), normaliza_e164(numero_sem_whatsapp_conhecido)]
    task = _submeter_task(nums, chave)
    task_final = _aguardar_resultado(task["task_id"], chave)
    ativados = _baixar_resultados(task_final["result_url"])
    return {
        numero_com_whatsapp_conhecido: ativados.get(nums[0].lstrip("+")),
        numero_sem_whatsapp_conhecido: ativados.get(nums[1].lstrip("+")),
        "custo": (task_final.get("actual_amount") or {}).get("amount"),
    }
