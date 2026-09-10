"""
Verificação de titularidade de telefone via Sherlocker (reverse-lookup).

Roda entre a "Etapa 2 — Dados cadastrais e decisor" (join_leads_socios.sql)
e a etapa 3 de enriquecimento por IA (enrich_leads.py / agentic_enrich.py):

  1. Para cada lead, verifica de quem é o telefone que a Receita Federal
     declarou (telefone_1 / telefone_2) via GET /pessoas/telefone/{numero}.
  2. Se algum CPF retornado bater com o documento_decisor (do sócio
     identificado em socios_decisor.sql), o telefone da empresa já É do
     decisor — confirma e não precisa buscar mais nada.
  3. Se não bater (ou não houver telefone declarado) e houver um decisor
     identificado, busca telefones do próprio CPF dele via
     GET /telefones/cpf/{cpf} e usa até 2 números encontrados.

Usa a API REST oficial do Sherlocker (Bearer token no header Authorization),
não o servidor MCP — o MCP é documentado como voltado a clientes interativos
(Claude Desktop, Cursor, etc.), não a scripts em lote de milhares de
consultas; a REST direta também é a única forma de controlar rate limit e
retry aqui.

ATENÇÃO — dado pessoal / LGPD: este script identifica o titular de um
número de telefone e consulta telefones de uma pessoa física por CPF a
partir de um serviço de terceiros (data broker). Isso é uma exceção
deliberada e avaliada para este fluxo específico (confirmar contato
comercial de um provável decisor de uma empresa já filtrada como lead B2B)
— não é uma licença geral para consultar CPF de qualquer pessoa, e não vale
para o agente de busca livre (enrich_leads.py / agentic_enrich.py seguem
proibidos de usar esse tipo de fonte). Trate a saída como dado sensível:
mesma política de acesso restrito ao time comercial e descarte quando o
lead não avançar, já descrita no guia para dados de sócios.

Requisitos: SHERLOCKER_API_KEY (variável de ambiente — NUNCA commitar a
chave no código ou em um arquivo versionado).
Doc: https://docs.sherlocker.com.br/guias/integracao-api

Uso:
    export SHERLOCKER_API_KEY="..."
    python verificar_telefone_decisor.py leads_com_socios.csv leads_com_telefone_decisor.csv
"""

import csv
import os
import re
import sys
import time
from dataclasses import asdict, dataclass

import requests

BASE_URL = "https://221b-api.sherlocker.com.br/api/v1"
MAX_TELEFONES_DECISOR = 2

# Limite documentado do plano padrão: 60 requests/minuto.
SECONDS_BETWEEN_CALLS = 1.1
MAX_RETRIES_429 = 5


def normaliza_documento(doc: str) -> str:
    return re.sub(r"\D", "", doc or "")


def _request(session: requests.Session, path: str, api_key: str) -> dict | None:
    url = f"{BASE_URL}{path}"
    for tentativa in range(1, MAX_RETRIES_429 + 1):
        time.sleep(SECONDS_BETWEEN_CALLS)
        resp = session.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
        if resp.status_code == 429:
            espera = float(resp.headers.get("Retry-After", 2 * tentativa))
            print(f"[aviso] rate limit (429) em {path}, esperando {espera:.0f}s...", file=sys.stderr)
            time.sleep(espera)
            continue
        if resp.status_code == 404:
            return None
        if resp.status_code == 401:
            raise SystemExit("Sherlocker: token ausente ou inválido (401). Confira SHERLOCKER_API_KEY.")
        if resp.status_code == 402:
            raise SystemExit("Sherlocker: saldo de tokens insuficiente (402).")
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"esgotou tentativas de rate limit em {path}")


def pessoas_por_telefone(session: requests.Session, telefone: str, api_key: str) -> list[dict]:
    data = _request(session, f"/pessoas/telefone/{telefone}", api_key)
    return (data or {}).get("pessoas", [])


def telefones_por_cpf(session: requests.Session, cpf: str, api_key: str) -> list[dict]:
    data = _request(session, f"/telefones/cpf/{cpf}", api_key)
    return (data or {}).get("telefones", [])


@dataclass
class ResultadoTelefone:
    titular_telefone_nome: str
    titular_telefone_documento: str
    telefone_confere_decisor: str
    telefone_decisor_sherlocker_1: str
    telefone_decisor_sherlocker_2: str
    motivo_telefone_decisor: str


def verificar_lead(session: requests.Session, lead: dict, api_key: str) -> ResultadoTelefone:
    cnpj = lead.get("cnpj", "")
    documento_decisor = normaliza_documento(lead.get("documento_decisor", ""))

    telefones_empresa = []
    for sufixo in ("1", "2"):
        ddd = (lead.get(f"ddd_{sufixo}") or "").strip()
        numero = (lead.get(f"telefone_{sufixo}") or "").strip()
        if ddd and numero:
            telefones_empresa.append(re.sub(r"\D", "", ddd + numero))

    titular_nome, titular_doc = "", ""
    algum_telefone_verificado = False
    houve_erro = False

    for telefone in telefones_empresa:
        try:
            pessoas = pessoas_por_telefone(session, telefone, api_key)
        except SystemExit:
            raise
        except Exception as exc:
            houve_erro = True
            print(f"[aviso] busca de telefone falhou p/ {cnpj} ({telefone}): {exc}", file=sys.stderr)
            continue

        algum_telefone_verificado = True
        for pessoa in pessoas:
            if not titular_nome:
                titular_nome = pessoa.get("nome", "")
                titular_doc = pessoa.get("documento", "")
            if documento_decisor and normaliza_documento(pessoa.get("documento", "")) == documento_decisor:
                return ResultadoTelefone(
                    titular_telefone_nome=pessoa.get("nome", ""),
                    titular_telefone_documento=pessoa.get("documento", ""),
                    telefone_confere_decisor="sim",
                    telefone_decisor_sherlocker_1="",
                    telefone_decisor_sherlocker_2="",
                    motivo_telefone_decisor="telefone_da_empresa_e_do_decisor",
                )

    if not documento_decisor:
        return ResultadoTelefone(
            titular_telefone_nome=titular_nome,
            titular_telefone_documento=titular_doc,
            telefone_confere_decisor="sem_decisor_identificado",
            telefone_decisor_sherlocker_1="",
            telefone_decisor_sherlocker_2="",
            motivo_telefone_decisor="sem_decisor_identificado",
        )

    confere = "nao" if algum_telefone_verificado else "sem_telefone_declarado"

    try:
        telefones_decisor = telefones_por_cpf(session, documento_decisor, api_key)
    except SystemExit:
        raise
    except Exception as exc:
        houve_erro = True
        print(f"[aviso] busca de telefones por CPF falhou p/ {cnpj}: {exc}", file=sys.stderr)
        telefones_decisor = []

    escolhidos = [t.get("numero_completo", "") for t in telefones_decisor[:MAX_TELEFONES_DECISOR]]
    while len(escolhidos) < MAX_TELEFONES_DECISOR:
        escolhidos.append("")

    if any(escolhidos):
        motivo = "telefone_via_cpf_decisor"
    elif houve_erro:
        motivo = "erro_consulta_sherlocker"
    else:
        motivo = "nenhum_telefone_encontrado_para_decisor"

    return ResultadoTelefone(
        titular_telefone_nome=titular_nome,
        titular_telefone_documento=titular_doc,
        telefone_confere_decisor=confere,
        telefone_decisor_sherlocker_1=escolhidos[0],
        telefone_decisor_sherlocker_2=escolhidos[1],
        motivo_telefone_decisor=motivo,
    )


def main(input_path: str, output_path: str) -> None:
    api_key = os.environ.get("SHERLOCKER_API_KEY")
    if not api_key:
        raise SystemExit("Defina a variável de ambiente SHERLOCKER_API_KEY antes de rodar (nunca no código).")

    with open(input_path, newline="", encoding="utf-8") as f_in:
        leads = list(csv.DictReader(f_in))
    if not leads:
        print("Nenhum lead no CSV de entrada.", file=sys.stderr)
        return

    done_cnpjs = set()
    if os.path.exists(output_path):
        with open(output_path, newline="", encoding="utf-8") as f_prev:
            for row in csv.DictReader(f_prev):
                done_cnpjs.add(row["cnpj"])

    colunas_novas = list(ResultadoTelefone.__dataclass_fields__.keys())
    fieldnames = list(leads[0].keys()) + colunas_novas
    write_header = not os.path.exists(output_path)

    with requests.Session() as session, open(output_path, "a", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        for i, lead in enumerate(leads, start=1):
            cnpj = lead.get("cnpj", "")
            if cnpj in done_cnpjs:
                continue
            nome_exibicao = lead.get("razao_social") or cnpj
            print(f"[{i}/{len(leads)}] verificando telefone de {nome_exibicao}...", file=sys.stderr, flush=True)

            resultado = verificar_lead(session, lead, api_key)
            writer.writerow({**lead, **asdict(resultado)})
            f_out.flush()

    print("CONCLUIDO", file=sys.stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", help="CSV de entrada (saída de join_leads_socios.sql)")
    parser.add_argument("saida", help="CSV de saída (retomável: pula CNPJs já presentes)")
    args = parser.parse_args()
    main(args.entrada, args.saida)
