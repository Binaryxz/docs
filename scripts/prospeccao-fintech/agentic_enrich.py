"""
Agente de enriquecimento com busca ativa (ReAct-style function calling).

Diferente de um grounding de busca única, aqui o Gemini decide sozinho:
  - quantas buscas fazer,
  - com quais termos (nome + cidade, depois nome + "telefone", depois
    nome + "whatsapp", etc.),
  - quando já tem confiança suficiente pra responder.

Duas ferramentas são expostas ao modelo:
  - buscar_web(query)        -> resultados do Brave Search (título, url, resumo)
  - registrar_resultado(...) -> finaliza a investigação desta empresa

O loop roda até o modelo chamar registrar_resultado ou esgotar
--max-searches (orçamento de segurança pra não ficar buscando pra sempre
numa empresa difícil de achar).

Requisitos: GEMINI_API_KEY (grátis, sem billing — não usamos grounding
nativo aqui) e BRAVE_API_KEY (plano gratuito em https://brave.com/search/api/).

Uso:
    export GEMINI_API_KEY="..."
    export BRAVE_API_KEY="..."
    python agentic_enrich.py leads.csv leads_enriquecidos.csv

Entrada esperada: um CSV com pelo menos uma coluna identificadora
('lead_id' ou 'cnpj') e quaisquer outras colunas com dados conhecidos
da empresa (nome, cidade, estado, descrição, redes sociais, etc.) —
o agente usa tudo que estiver preenchido como contexto de busca.
"""

import csv
import os
import sys
import time
from dataclasses import asdict, dataclass

import requests
from google import genai
from google.genai import types

MODEL = "gemini-flash-latest"
MAX_SEARCHES_PADRAO = 5
SECONDS_BETWEEN_LEADS = 1.0
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="buscar_web",
                description=(
                    "Pesquisa no Brave Search e retorna os principais resultados "
                    "(título, url, resumo). Use termos específicos e variados: "
                    "nome da empresa + cidade, nome + 'site oficial', nome + "
                    "'telefone', nome + 'whatsapp', etc."
                ),
                parameters={
                    "type": "OBJECT",
                    "properties": {"query": {"type": "STRING"}},
                    "required": ["query"],
                },
            ),
            types.FunctionDeclaration(
                name="registrar_resultado",
                description=(
                    "Registra o resultado final da investigação desta empresa. "
                    "Chame só quando tiver terminado de pesquisar (achou o que "
                    "precisava ou esgotou as buscas razoáveis)."
                ),
                parameters={
                    "type": "OBJECT",
                    "properties": {
                        "site_oficial": {"type": "STRING", "description": "URL do site oficial, ou vazio"},
                        "telefone_comercial_ia": {"type": "STRING", "description": "Telefone comercial público, ou vazio"},
                        "whatsapp_publico": {"type": "STRING", "description": "WhatsApp comercial público, ou vazio"},
                        "fonte_ia": {
                            "type": "STRING",
                            "description": "URLs reais (das buscas que você fez) que confirmam os dados acima, separadas por ' | '",
                        },
                        "confianca_ia": {"type": "STRING", "enum": ["alta", "media", "baixa"]},
                    },
                    "required": ["site_oficial", "telefone_comercial_ia", "whatsapp_publico", "fonte_ia", "confianca_ia"],
                },
            ),
        ]
    )
]

SYSTEM_INSTRUCTION = """\
Você é um agente de pesquisa que investiga empresas brasileiras usando a \
ferramenta buscar_web (Brave Search). Seu objetivo é achar, se existirem \
publicamente: site oficial, telefone comercial público e WhatsApp comercial \
público.

Estratégia recomendada:
1. Comece buscando o nome da empresa + cidade/estado pra achar o site oficial.
2. Se achar o site (ou uma rede social oficial), faça buscas mais específicas \
   pra confirmar telefone/WhatsApp (ex.: "<nome> contato telefone", \
   "<nome> whatsapp comercial").
3. Se depois de várias tentativas razoáveis não achar nada confiável, pare \
   e registre os campos vazios com confiança "baixa" — não fique insistindo \
   à toa.
4. A empresa pode ter fechado ou mudado de nome desde os dados que você tem. \
   Se as buscas sugerirem isso, registre confiança "baixa".

Regras inegociáveis:
- NUNCA invente site, telefone ou WhatsApp. Se não achou uma fonte pública \
  real, deixe o campo vazio ("").
- Em fonte_ia, cite APENAS URLs que realmente vieram dos resultados de \
  buscar_web nesta investigação.
- Você tem no máximo {max_searches} chamadas de buscar_web para esta empresa. \
  Use-as com inteligência (termos diferentes a cada busca, não repita a \
  mesma query).
- Quando terminar, sua ÚLTIMA ação deve ser chamar registrar_resultado.
"""


@dataclass
class Enrichment:
    lead_id: str
    site_oficial: str
    telefone_comercial_ia: str
    whatsapp_publico: str
    fonte_ia: str
    confianca_ia: str
    buscas_realizadas: str


def brave_search(query: str, api_key: str, count: int = 5) -> list[dict]:
    resp = requests.get(
        BRAVE_ENDPOINT,
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        params={"q": query, "count": count},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    resultados = []
    for item in data.get("web", {}).get("results", [])[:count]:
        resultados.append(
            {
                "titulo": item.get("title", ""),
                "url": item.get("url", ""),
                "resumo": item.get("description", ""),
            }
        )
    return resultados


def build_lead_context(lead: dict) -> str:
    linhas = []
    for k, v in lead.items():
        if k == "lead_id" or not v or str(v).strip() == "":
            continue
        linhas.append(f"- {k}: {v}")
    return "\n".join(linhas) if linhas else "(nenhum dado adicional disponível)"


def enrich_one(client: genai.Client, brave_api_key: str, lead: dict, max_searches: int) -> Enrichment:
    lead_id = lead.get("lead_id") or lead.get("cnpj") or ""
    prompt = f"Dados conhecidos desta empresa brasileira:\n{build_lead_context(lead)}\n\nInvestigue e registre o resultado."
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
    buscas_feitas: list[str] = []

    for _ in range(max_searches + 2):  # +2 de folga pra permitir o registrar_resultado final
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION.format(max_searches=max_searches),
                    tools=TOOLS,
                ),
            )
        except Exception as exc:
            print(f"[aviso] erro de API em lead_id={lead_id}: {exc}", file=sys.stderr)
            time.sleep(3)
            continue

        candidate = response.candidates[0]
        contents.append(candidate.content)

        function_calls = [p.function_call for p in candidate.content.parts if p.function_call]
        if not function_calls:
            break

        final_args = None
        response_parts = []
        for fc in function_calls:
            if fc.name == "registrar_resultado":
                final_args = dict(fc.args)
            elif fc.name == "buscar_web":
                query = (fc.args or {}).get("query", "")
                if len(buscas_feitas) >= max_searches:
                    resultados = []
                else:
                    try:
                        resultados = brave_search(query, brave_api_key)
                    except Exception as exc:
                        resultados = []
                        print(f"[aviso] busca falhou ({query!r}): {exc}", file=sys.stderr)
                    buscas_feitas.append(query)
                response_parts.append(
                    types.Part.from_function_response(name="buscar_web", response={"resultados": resultados})
                )

        if final_args is not None:
            return Enrichment(
                lead_id=lead_id,
                site_oficial=final_args.get("site_oficial", ""),
                telefone_comercial_ia=final_args.get("telefone_comercial_ia", ""),
                whatsapp_publico=final_args.get("whatsapp_publico", ""),
                fonte_ia=final_args.get("fonte_ia", ""),
                confianca_ia=final_args.get("confianca_ia", "baixa"),
                buscas_realizadas=" | ".join(buscas_feitas),
            )

        contents.append(types.Content(role="user", parts=response_parts))

    return Enrichment(
        lead_id=lead_id,
        site_oficial="",
        telefone_comercial_ia="",
        whatsapp_publico="",
        fonte_ia="",
        confianca_ia="baixa",
        buscas_realizadas=" | ".join(buscas_feitas),
    )


def main(input_path: str, output_path: str, max_searches: int = MAX_SEARCHES_PADRAO) -> None:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    brave_api_key = os.environ["BRAVE_API_KEY"]

    with open(input_path, newline="", encoding="utf-8") as f_in:
        leads = list(csv.DictReader(f_in))
    if not leads:
        print("Nenhum lead no CSV de entrada.", file=sys.stderr)
        return

    id_col = "lead_id" if "lead_id" in leads[0] else "cnpj"

    done_ids = set()
    if os.path.exists(output_path):
        with open(output_path, newline="", encoding="utf-8") as f_prev:
            for row in csv.DictReader(f_prev):
                done_ids.add(row["lead_id"])

    fieldnames = ["lead_id", "site_oficial", "telefone_comercial_ia", "whatsapp_publico", "fonte_ia", "confianca_ia", "buscas_realizadas"]
    write_header = not os.path.exists(output_path)

    with open(output_path, "a", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()

        for i, lead in enumerate(leads, start=1):
            current_id = lead.get(id_col, "")
            if current_id in done_ids:
                continue
            nome_exibicao = lead.get("nome") or lead.get("razao_social") or current_id
            print(f"[{i}/{len(leads)}] investigando {nome_exibicao}...", file=sys.stderr, flush=True)
            enrichment = enrich_one(client, brave_api_key, lead, max_searches)
            writer.writerow(asdict(enrichment))
            f_out.flush()
            time.sleep(SECONDS_BETWEEN_LEADS)

    print("CONCLUIDO", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print("Uso: python agentic_enrich.py <entrada.csv> <saida.csv> [max_buscas_por_empresa]", file=sys.stderr)
        sys.exit(1)
    max_searches = int(sys.argv[3]) if len(sys.argv) == 4 else MAX_SEARCHES_PADRAO
    main(sys.argv[1], sys.argv[2], max_searches)
