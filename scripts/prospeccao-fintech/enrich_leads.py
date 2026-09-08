"""
Agente de IA que enriquece cada lead com dados públicos encontrados na web.

Para cada empresa (razao_social + uf/municipio como contexto), pede ao Gemini
para pesquisar no Google (grounding nativo, sem scraping manual) e devolver:
  - site oficial
  - telefone comercial público
  - WhatsApp comercial público
  - as fontes (URLs) de onde cada dado veio

Uso:
    export GEMINI_API_KEY="..."
    python enrich_leads.py leads_filtrados.csv leads_enriquecidos.csv

Entrada esperada (CSV): colunas cnpj, razao_social, nome_fantasia, uf, municipio
(o que sai de join_leads_socios.sql). A coluna nome_decisor, se presente, é
usada apenas como contexto extra de busca (ajuda a desambiguar empresas com
nome genérico) e não é obrigatória.
"""

import csv
import json
import sys
import time
from dataclasses import dataclass, asdict

from google import genai
from google.genai import types

MODEL = "gemini-2.5-flash"
MAX_RETRIES = 3
SECONDS_BETWEEN_CALLS = 1.0  # respeita rate limit da API

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "site_oficial": {"type": "STRING", "description": "URL do site oficial, ou vazio se não encontrado"},
        "telefone_comercial_ia": {"type": "STRING", "description": "Telefone comercial público, ou vazio"},
        "whatsapp_publico": {"type": "STRING", "description": "Número de WhatsApp comercial público, ou vazio"},
        "confianca_ia": {"type": "STRING", "enum": ["alta", "media", "baixa"]},
    },
    "required": ["site_oficial", "telefone_comercial_ia", "whatsapp_publico", "confianca_ia"],
}

PROMPT_TEMPLATE = """\
Pesquise no Google a empresa brasileira abaixo e encontre, se existirem publicamente:
1. O site oficial da empresa
2. Um telefone comercial público
3. Um número de WhatsApp comercial público (linha divulgada em site, Google Meu Negócio, etc.)

Empresa: {razao_social}
Nome fantasia: {nome_fantasia}
CNPJ: {cnpj}
Localização: {municipio}/{uf}
{decisor_linha}
Regras:
- Não invente nada. Se não encontrar algum dado com uma fonte pública confiável, deixe o campo vazio ("").
- "confianca_ia" = "alta" se o site oficial bate com a razão social/CNPJ; "media" se é plausível mas não 100% confirmado; "baixa" se os dados são incertos.
Responda apenas no formato JSON pedido.
"""


@dataclass
class Enrichment:
    cnpj: str
    site_oficial: str
    telefone_comercial_ia: str
    whatsapp_publico: str
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

    prompt = PROMPT_TEMPLATE.format(
        razao_social=lead.get("razao_social", ""),
        nome_fantasia=lead.get("nome_fantasia", ""),
        cnpj=lead.get("cnpj", ""),
        municipio=lead.get("municipio", ""),
        uf=lead.get("uf", ""),
        decisor_linha=decisor_linha,
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
        fonte_ia="",
        confianca_ia="baixa",
    )


def main(input_path: str, output_path: str) -> None:
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
        "fonte_ia",
        "confianca_ia",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()

        for i, lead in enumerate(leads, start=1):
            print(f"[{i}/{len(leads)}] pesquisando {lead.get('razao_social')}...", file=sys.stderr)
            enrichment = enrich_one(client, lead)
            writer.writerow({**lead, **asdict(enrichment)})
            f_out.flush()
            time.sleep(SECONDS_BETWEEN_CALLS)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Uso: python enrich_leads.py <entrada.csv> <saida.csv>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
