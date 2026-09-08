"""
Agente de IA que enriquece cada lead com dados públicos encontrados na web.

Para cada empresa (razao_social + uf/municipio como contexto), pede ao Gemini
para pesquisar no Google (grounding nativo, sem scraping manual) e devolver:
  - site oficial
  - telefone comercial público
  - WhatsApp comercial público
  - se um decisor foi informado (nome_decisor): um contato profissional
    público dele (perfil do LinkedIn e/ou telefone, quando publicados)
  - as fontes (URLs) de onde cada dado veio

Uso:
    export GEMINI_API_KEY="..."
    python enrich_leads.py leads_filtrados.csv leads_enriquecidos.csv
    python enrich_leads.py leads.csv saida.csv --pular-com-contato telefone_1,correio_eletronico

Entrada esperada (CSV): colunas cnpj, razao_social, nome_fantasia, uf, municipio
(o que sai de join_leads_socios.sql). As colunas nome_decisor e descricao, se
presentes, são usadas apenas como contexto extra de busca (ajudam a
desambiguar empresas com nome genérico) e não são obrigatórias.

Roda de novo em cima do mesmo arquivo de saída para retomar de onde parou:
CNPJs já presentes em <saida.csv> são pulados.
"""

import csv
import json
import os
import sys
import time
from dataclasses import dataclass, asdict

from google import genai
from google.genai import types

MODEL = "gemini-flash-latest"
MAX_RETRIES = 3
SECONDS_BETWEEN_CALLS = 1.0  # respeita rate limit da API

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "site_oficial": {"type": "STRING", "description": "URL do site oficial, ou vazio se não encontrado"},
        "telefone_comercial_ia": {"type": "STRING", "description": "Telefone comercial público, ou vazio"},
        "whatsapp_publico": {"type": "STRING", "description": "Número de WhatsApp comercial público, ou vazio"},
        "linkedin_decisor": {
            "type": "STRING",
            "description": "URL do perfil do LinkedIn do decisor informado, ou vazio se não houver decisor ou não for encontrado",
        },
        "telefone_decisor_ia": {
            "type": "STRING",
            "description": "Telefone público do decisor informado (raramente público — só preencha com fonte confirmada), ou vazio",
        },
        "confianca_ia": {"type": "STRING", "enum": ["alta", "media", "baixa"]},
    },
    "required": [
        "site_oficial",
        "telefone_comercial_ia",
        "whatsapp_publico",
        "linkedin_decisor",
        "telefone_decisor_ia",
        "confianca_ia",
    ],
}

PROMPT_TEMPLATE = """\
Pesquise no Google a empresa brasileira abaixo e encontre, se existirem publicamente:
1. O site oficial da empresa
2. Um telefone comercial público
3. Um número de WhatsApp comercial público (linha divulgada em site, Google Meu Negócio, etc.)
4. Se um possível decisor foi indicado abaixo, tente achar um contato profissional público dele: o perfil do LinkedIn é a fonte mais confiável; telefone pessoal raramente é público, só preencha "telefone_decisor_ia" se achar um número explicitamente associado a essa pessoa em fonte pública (ex.: assinatura de e-mail publicada, cartão de visita digital, perfil profissional).

Empresa: {razao_social}
Nome fantasia: {nome_fantasia}
CNPJ: {cnpj}
Localização: {municipio}/{uf}
{decisor_linha}{descricao_linha}
Regras:
- Não invente nada. Se não encontrar algum dado com uma fonte pública confiável, deixe o campo vazio ("").
- Se nenhum decisor foi indicado acima, deixe "linkedin_decisor" e "telefone_decisor_ia" vazios.
- "confianca_ia" = "alta" se o site oficial bate com a razão social/CNPJ; "media" se é plausível mas não 100% confirmado; "baixa" se os dados são incertos.
Responda apenas no formato JSON pedido.
"""


@dataclass
class Enrichment:
    cnpj: str
    site_oficial: str
    telefone_comercial_ia: str
    whatsapp_publico: str
    linkedin_decisor: str
    telefone_decisor_ia: str
    fonte_ia: str
    confianca_ia: str


def extract_sources(response) -> str:
    """Junta as URLs usadas pelo grounding do Gemini como string separada por ' | '."""
    urls = []
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        grounding_metadata = getattr(candidate, "grounding_metadata", None)
        if not grounding_metadata:
            continue
        for chunk in getattr(grounding_metadata, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            if web and getattr(web, "uri", None):
                urls.append(web.uri)
    return " | ".join(dict.fromkeys(urls))  # remove duplicadas mantendo ordem


def enrich_one(client: genai.Client, lead: dict) -> Enrichment:
    nome_decisor = lead.get("nome_decisor", "").strip()
    decisor_linha = f"Possível decisor (sócio-administrador/diretor): {nome_decisor}\n" if nome_decisor else ""

    descricao = lead.get("descricao", "").strip()
    descricao_linha = f"Descrição conhecida da empresa: {descricao}\n" if descricao else ""

    prompt = PROMPT_TEMPLATE.format(
        razao_social=lead.get("razao_social", ""),
        nome_fantasia=lead.get("nome_fantasia", ""),
        cnpj=lead.get("cnpj", ""),
        municipio=lead.get("municipio", ""),
        uf=lead.get("uf", ""),
        decisor_linha=decisor_linha,
        descricao_linha=descricao_linha,
    )

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                ),
            )
            data = json.loads(response.text)
            return Enrichment(
                cnpj=lead.get("cnpj", ""),
                site_oficial=data.get("site_oficial", ""),
                telefone_comercial_ia=data.get("telefone_comercial_ia", ""),
                whatsapp_publico=data.get("whatsapp_publico", ""),
                linkedin_decisor=data.get("linkedin_decisor", ""),
                telefone_decisor_ia=data.get("telefone_decisor_ia", ""),
                fonte_ia=extract_sources(response),
                confianca_ia=data.get("confianca_ia", "baixa"),
            )
        except Exception as exc:  # nosec - queremos seguir para o próximo lead mesmo com falha pontual
            last_error = exc
            time.sleep(SECONDS_BETWEEN_CALLS * attempt)

    print(f"[aviso] falha ao enriquecer {lead.get('cnpj')}: {last_error}", file=sys.stderr)
    return Enrichment(
        cnpj=lead.get("cnpj", ""),
        site_oficial="",
        telefone_comercial_ia="",
        whatsapp_publico="",
        linkedin_decisor="",
        telefone_decisor_ia="",
        fonte_ia="",
        confianca_ia="baixa",
    )


def main(input_path: str, output_path: str, pular_se_preenchido: list[str] | None = None) -> None:
    client = genai.Client()  # lê GEMINI_API_KEY do ambiente

    with open(input_path, newline="", encoding="utf-8") as f_in:
        leads = list(csv.DictReader(f_in))

    if not leads:
        print("Nenhum lead encontrado no CSV de entrada.", file=sys.stderr)
        return

    fieldnames = list(leads[0].keys()) + [
        "site_oficial",
        "telefone_comercial_ia",
        "whatsapp_publico",
        "linkedin_decisor",
        "telefone_decisor_ia",
        "fonte_ia",
        "confianca_ia",
    ]

    done_cnpjs = set()
    if os.path.exists(output_path):
        with open(output_path, newline="", encoding="utf-8") as f_prev:
            for row in csv.DictReader(f_prev):
                done_cnpjs.add(row["cnpj"])

    write_header = not os.path.exists(output_path)

    with open(output_path, "a", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        for i, lead in enumerate(leads, start=1):
            cnpj = lead.get("cnpj", "")
            if cnpj and cnpj in done_cnpjs:
                continue

            if pular_se_preenchido and any(lead.get(campo, "").strip() for campo in pular_se_preenchido):
                print(f"[{i}/{len(leads)}] pulando {lead.get('razao_social')} (já tem contato na base)...", file=sys.stderr)
                pulado = Enrichment(
                    cnpj=cnpj, site_oficial="", telefone_comercial_ia="", whatsapp_publico="",
                    linkedin_decisor="", telefone_decisor_ia="", fonte_ia="",
                    confianca_ia="pulado_ja_tinha_contato",
                )
                writer.writerow({**lead, **asdict(pulado)})
                f_out.flush()
                continue

            print(f"[{i}/{len(leads)}] pesquisando {lead.get('razao_social')}...", file=sys.stderr)
            enrichment = enrich_one(client, lead)
            writer.writerow({**lead, **asdict(enrichment)})
            f_out.flush()
            time.sleep(SECONDS_BETWEEN_CALLS)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Enriquece leads com site/telefone/WhatsApp/decisor via Gemini (grounding nativo).")
    parser.add_argument("entrada", help="CSV de entrada (leads_com_socios.csv)")
    parser.add_argument("saida", help="CSV de saída (retomável: roda de novo em cima do mesmo arquivo)")
    parser.add_argument(
        "--pular-com-contato",
        metavar="COL1,COL2,...",
        default=None,
        help=(
            "Nomes de colunas do CSV de entrada (separados por vírgula) que, se já "
            "preenchidas, fazem o lead ser pulado sem gastar chamada de API — "
            "reduz custo quando a base já traz contato pra boa parte dos leads. "
            "Ex.: --pular-com-contato telefone_1,correio_eletronico"
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help=(
            f"Segundos entre chamadas (padrão {SECONDS_BETWEEN_CALLS}). Aumente se a conta "
            "bater 'spend-based rate limit' (429) — esse limite é por ritmo de gasto, não só "
            "por crédito total, então ir mais devagar evita o erro."
        ),
    )
    args = parser.parse_args()

    if args.delay is not None:
        SECONDS_BETWEEN_CALLS = args.delay

    pular = [c.strip() for c in args.pular_com_contato.split(",")] if args.pular_com_contato else None
    main(args.entrada, args.saida, pular_se_preenchido=pular)
