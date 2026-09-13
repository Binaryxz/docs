"""
Verificação de titularidade de telefone via Sherlocker (reverse-lookup).

Roda entre a "Etapa 3 — Dados cadastrais e decisor" (join_leads_socios.sql)
e a etapa 5 de enriquecimento por IA (enrich_leads.py / agentic_enrich.py):

  1. Para cada lead, verifica de quem é o telefone que a Receita Federal
     declarou (telefone_1 / telefone_2) via GET /pessoas/telefone/{numero}.
  2. Se a pessoa retornada for a mesma do documento_decisor (do sócio
     identificado em socios_decisor.sql), o telefone da empresa já É do
     decisor — confirma e não precisa buscar mais nada.
  3. Se não bater (ou não houver telefone declarado) e houver um decisor
     identificado, busca telefones do próprio CPF dele via
     GET /telefones/cpf/{cpf} e usa até 2 números encontrados.

ATENÇÃO — CPF mascarado: a Receita Federal mascara o CPF de sócio pessoa
física nos dados públicos (`documento_decisor` vem tipo `***257278**`, só 6
dos 11 dígitos visíveis — os 3 primeiros e os 2 últimos são ocultados). Uma
comparação exata de CPF entre `documento_decisor` e o CPF completo (não
mascarado) que o Sherlocker devolve NUNCA bate. Por isso a verificação
abaixo compara por NOME normalizado (sem acento, maiúsculas, ignorando
"DE/DA/DO" etc., exigindo pelo menos 2 tokens em comum) como critério
principal, e usa o CPF mascarado só como sinal auxiliar (compara os dígitos
visíveis nas mesmas posições, quando o formato de máscara é reconhecido).

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

A taxa de confirmação ("telefone da empresa = telefone pessoal do decisor")
é baixa por natureza — a maioria das empresas não declara o celular pessoal
do sócio como telefone oficial do CNPJ. Não interprete uma taxa baixa como
bug deste script.

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
import unicodedata
from dataclasses import asdict, dataclass

import requests

BASE_URL = "https://221b-api.sherlocker.com.br/api/v1"
MAX_TELEFONES_DECISOR = 2

# Limite documentado do plano padrão: 60 requests/minuto.
SECONDS_BETWEEN_CALLS = 1.1
MAX_RETRIES_429 = 5

TOKENS_IGNORADOS_NOME = {"DE", "DA", "DO", "DOS", "DAS", "E"}


def normaliza_documento(doc: str) -> str:
    return re.sub(r"\D", "", doc or "")


def normaliza_nome(nome: str) -> set[str]:
    nome = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode("ascii").upper()
    return {t for t in re.split(r"\s+", nome.strip()) if t and t not in TOKENS_IGNORADOS_NOME}


def mesma_pessoa_por_nome(nome_decisor: str, nome_candidato: str) -> bool:
    """Compara dois nomes normalizados, exigindo pelo menos 2 tokens em
    comum — mais robusto que CPF exato quando um dos lados vem mascarado
    (ver aviso no topo do arquivo)."""
    tokens_decisor = normaliza_nome(nome_decisor)
    tokens_candidato = normaliza_nome(nome_candidato)
    if len(tokens_decisor) < 2 or len(tokens_candidato) < 2:
        return False
    return len(tokens_decisor & tokens_candidato) >= 2


def documento_confere_mascarado(documento_receita: str, documento_completo: str) -> bool:
    """Compara só os dígitos VISÍVEIS de um CPF mascarado da Receita
    (ex.: ***257278**) contra um CPF completo de outra fonte, posição a
    posição — nunca compara os dígitos ocultados (`*`). Devolve False se o
    formato não parecer uma máscara reconhecível (evita falso positivo)."""
    bruto = (documento_receita or "").strip()
    completo = normaliza_documento(documento_completo)
    if len(bruto) != 11 or len(completo) != 11 or "*" not in bruto:
        return False
    for pos, char in enumerate(bruto):
        if char == "*":
            continue
        if not char.isdigit() or char != completo[pos]:
            return False
    return True


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
    nome_decisor = (lead.get("nome_decisor") or "").strip()
    documento_decisor_mascarado = (lead.get("documento_decisor") or "").strip()

    telefones_empresa = []
    for sufixo in ("1", "2"):
        ddd = (lead.get(f"ddd_{sufixo}") or "").strip()
        numero = (lead.get(f"telefone_{sufixo}") or "").strip()
        if ddd and numero:
            telefones_empresa.append(re.sub(r"\D", "", ddd + numero))

    titular_nome, titular_doc = "", ""
    algum_telefone_verificado = False
    houve_erro = False
    tem_decisor = bool(nome_decisor or documento_decisor_mascarado)

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

            confere_por_nome = nome_decisor and mesma_pessoa_por_nome(nome_decisor, pessoa.get("nome", ""))
            confere_por_doc = documento_decisor_mascarado and documento_confere_mascarado(
                documento_decisor_mascarado, pessoa.get("documento", "")
            )
            if confere_por_nome or confere_por_doc:
                return ResultadoTelefone(
                    titular_telefone_nome=pessoa.get("nome", ""),
                    titular_telefone_documento=pessoa.get("documento", ""),
                    telefone_confere_decisor="sim",
                    telefone_decisor_sherlocker_1="",
                    telefone_decisor_sherlocker_2="",
                    motivo_telefone_decisor="telefone_da_empresa_e_do_decisor",
                )

    if not tem_decisor:
        return ResultadoTelefone(
            titular_telefone_nome=titular_nome,
            titular_telefone_documento=titular_doc,
            telefone_confere_decisor="sem_decisor_identificado",
            telefone_decisor_sherlocker_1="",
            telefone_decisor_sherlocker_2="",
            motivo_telefone_decisor="sem_decisor_identificado",
        )

    confere = "nao" if algum_telefone_verificado else "sem_telefone_declarado"

    documento_para_busca = normaliza_documento(documento_decisor_mascarado)
    telefones_decisor: list[dict] = []
    if len(documento_para_busca) == 11 and "*" not in documento_decisor_mascarado:
        # só dá pra buscar telefones por CPF quando ele NÃO estiver
        # mascarado (a API do Sherlocker exige os 11 dígitos completos) —
        # ver aviso no topo do arquivo.
        try:
            telefones_decisor = telefones_por_cpf(session, documento_para_busca, api_key)
        except SystemExit:
            raise
        except Exception as exc:
            houve_erro = True
            print(f"[aviso] busca de telefones por CPF falhou p/ {cnpj}: {exc}", file=sys.stderr)

    escolhidos = [t.get("numero_completo", "") for t in telefones_decisor[:MAX_TELEFONES_DECISOR]]
    while len(escolhidos) < MAX_TELEFONES_DECISOR:
        escolhidos.append("")

    if any(escolhidos):
        motivo = "telefone_via_cpf_decisor"
    elif houve_erro:
        motivo = "erro_consulta_sherlocker"
    elif "*" in documento_decisor_mascarado:
        motivo = "cpf_do_decisor_mascarado_na_receita"
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
