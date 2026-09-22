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
MAX_SEARCHES_PADRAO = 7  # empresa + pelo menos 2 buscas dedicadas ao decisor, quando houver um
SECONDS_BETWEEN_LEADS = 1.0
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

# O nível gratuito do Gemini limita a poucas requisições por minuto por modelo
# (na prática, ~5 RPM em modelos flash no momento em que este script foi
# escrito — pode mudar). Cada chamada do agente (busca ou decisão final) é
# uma requisição, então espaçamos as chamadas pra não estourar o limite e
# tratar 429 com o retryDelay real que a API devolve, em vez de um backoff
# arbitrário que desperdiça tentativas.
SECONDS_BETWEEN_API_CALLS = 13.0
DEFAULT_RETRY_DELAY = 20.0
MAX_RETRIES_TRANSIENTES = 6

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
                        "linkedin_decisor": {
                            "type": "STRING",
                            "description": "URL do perfil do LinkedIn do sócio/decisor indicado no contexto, ou vazio se não houver decisor ou não achar",
                        },
                        "telefone_decisor_ia": {
                            "type": "STRING",
                            "description": "Telefone/WhatsApp pessoal do decisor, SÓ se achar uma fonte pública que associe explicitamente esse número a essa pessoa (raro); caso contrário vazio",
                        },
                        "fonte_ia": {
                            "type": "STRING",
                            "description": "URLs reais (das buscas que você fez) que confirmam os dados acima, separadas por ' | '",
                        },
                        "confianca_ia": {"type": "STRING", "enum": ["alta", "media", "baixa"]},
                    },
                    "required": [
                        "site_oficial",
                        "telefone_comercial_ia",
                        "whatsapp_publico",
                        "linkedin_decisor",
                        "telefone_decisor_ia",
                        "fonte_ia",
                        "confianca_ia",
                    ],
                },
            ),
        ]
    )
]

SYSTEM_INSTRUCTION = """\
Você é um agente de pesquisa que investiga empresas brasileiras usando a \
ferramenta buscar_web (Brave Search). Seu objetivo é achar, se existirem \
publicamente: site oficial, telefone comercial público, WhatsApp comercial \
público e, se um sócio/decisor foi indicado no contexto da empresa, o \
LinkedIn (e, mais raramente, telefone pessoal) dessa pessoa.

Estratégia recomendada:
1. Comece buscando o nome da empresa + cidade/estado pra achar o site oficial.
2. Se achar o site (ou uma rede social oficial), faça buscas mais específicas \
   pra confirmar telefone/WhatsApp (ex.: "<nome da empresa> contato telefone", \
   "<nome da empresa> whatsapp comercial").
3. Se o contexto trouxer um nome de sócio/decisor, dedique PELO MENOS 3 \
   buscas específicas a essa PESSOA (não à empresa), variando os termos: \
   "<nome do decisor> linkedin", "<nome do decisor> <nome da empresa>", \
   "<nome do decisor> whatsapp", "<nome do decisor> contato". O LinkedIn é \
   a fonte mais confiável e mais provável de existir — priorize achar o \
   perfil. WhatsApp/telefone pessoal é raro de ser público; só registre se \
   uma fonte PÚBLICA E LEGÍTIMA associar explicitamente aquele número a \
   essa pessoa (ex.: bio de rede social dela, post/anúncio dela mesma \
   divulgando o contato, assinatura de e-mail publicada, matéria de \
   imprensa, perfil profissional com contato) — nunca infira ou reutilize \
   o telefone comercial da empresa como se fosse o telefone pessoal do \
   decisor.
4. Se depois de várias tentativas razoáveis não achar nada confiável, pare \
   e registre os campos vazios com confiança "baixa" — não fique insistindo \
   à toa.
5. A empresa pode ter fechado ou mudado de nome desde os dados que você tem. \
   Se as buscas sugerirem isso, registre confiança "baixa".

Regras inegociáveis:
- NUNCA invente site, telefone, WhatsApp ou LinkedIn. Se não achou uma fonte \
  pública real, deixe o campo vazio ("").
- NUNCA copie o telefone/WhatsApp comercial da empresa para o campo de \
  telefone do decisor — são coisas diferentes, mesmo que pertençam à mesma \
  pessoa em empresas pequenas; só preencha o campo do decisor com um número \
  explicitamente atribuído a ele/ela como pessoa.
- NUNCA use nem cite resultados de sites de "consulta de CPF", "busca de \
  telefone", "achar pessoa", data brokers, ou qualquer serviço que venda/\
  agregue dados pessoais de terceiros sem que a própria pessoa os tenha \
  publicado. Só valem fontes onde a PRÓPRIA pessoa divulgou o dado \
  publicamente (rede social dela, site dela, perfil profissional dela). \
  (A única exceção no pipeline é a etapa dedicada verificar_telefone_decisor.py, \
  que usa o Sherlocker de forma controlada e auditável para confirmar \
  titularidade de telefone — isso NÃO abre exceção para você, agente de \
  busca livre, usar esse tipo de fonte.)
- Se nenhum decisor foi indicado no contexto, deixe linkedin_decisor e \
  telefone_decisor_ia vazios.
- Em fonte_ia, cite APENAS URLs que realmente vieram dos resultados de \
  buscar_web nesta investigação.
- Você tem no máximo {max_searches} chamadas de buscar_web para esta empresa. \
  Use-as com inteligência (termos diferentes a cada busca, não repita a \
  mesma query, e reserve parte do orçamento para o decisor quando houver um).
- Quando terminar, sua ÚLTIMA ação deve ser chamar registrar_resultado.
"""


@dataclass
class Enrichment:
    lead_id: str
    site_oficial: str
    telefone_comercial_ia: str
    whatsapp_publico: str
    linkedin_decisor: str
    telefone_decisor_ia: str
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


def _extrair_retry_delay(exc: Exception) -> float:
    """Tenta ler o retryDelay que a API manda no erro 429; senão usa um default."""
    import re

    m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)", str(exc))
    if m:
        return float(m.group(1)) + 1.0  # +1s de folga
    return DEFAULT_RETRY_DELAY


def _generate_com_retry(client: genai.Client, contents, max_searches: int):
    """Chama generate_content tratando 429 (rate limit) e 503 (sobrecarga) com
    o tempo de espera certo, sem consumir o orçamento de buscas do agente."""
    for tentativa in range(1, MAX_RETRIES_TRANSIENTES + 1):
        try:
            return client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION.format(max_searches=max_searches),
                    tools=TOOLS,
                ),
            )
        except Exception as exc:
            texto_erro = str(exc)
            if "429" in texto_erro:
                espera = _extrair_retry_delay(exc)
            elif "503" in texto_erro or "UNAVAILABLE" in texto_erro:
                espera = 5.0 * tentativa
            else:
                raise
            print(f"[aviso] erro transiente ({tentativa}/{MAX_RETRIES_TRANSIENTES}), esperando {espera:.0f}s: {texto_erro[:150]}", file=sys.stderr)
            time.sleep(espera)
    raise RuntimeError("esgotou as tentativas após erros transientes repetidos")


def enrich_one(client: genai.Client, brave_api_key: str, lead: dict, max_searches: int) -> Enrichment:
    lead_id = lead.get("lead_id") or lead.get("cnpj") or ""
    prompt = f"Dados conhecidos desta empresa brasileira:\n{build_lead_context(lead)}\n\nInvestigue e registre o resultado."
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]
    buscas_feitas: list[str] = []

    ja_pediu_para_finalizar = False
    for _ in range(max_searches + 3):  # +3 de folga: registrar_resultado final + 1 empurrão se necessário
        time.sleep(SECONDS_BETWEEN_API_CALLS)
        try:
            response = _generate_com_retry(client, contents, max_searches)
        except RuntimeError as exc:
            print(f"[aviso] desistindo de lead_id={lead_id}: {exc}", file=sys.stderr)
            break

        candidate = response.candidates[0]
        contents.append(candidate.content)

        function_calls = [p.function_call for p in candidate.content.parts if p.function_call]
        if not function_calls:
            if ja_pediu_para_finalizar:
                break
            # o modelo respondeu em texto livre em vez de chamar uma função
            # (ex.: concluiu que não vai achar mais nada) — dá uma última
            # chance de registrar o que já apurou em vez de descartar tudo
            ja_pediu_para_finalizar = True
            contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(
                        text="Finalize agora chamando registrar_resultado com o que você já apurou (campos vazios para o que não encontrou)."
                    )],
                )
            )
            continue

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
                linkedin_decisor=final_args.get("linkedin_decisor", ""),
                telefone_decisor_ia=final_args.get("telefone_decisor_ia", ""),
                fonte_ia=final_args.get("fonte_ia", ""),
                confianca_ia=final_args.get("confianca_ia", "baixa"),
                buscas_realizadas=" | ".join(buscas_feitas),
            )

        if len(buscas_feitas) >= max_searches:
            # orçamento de buscas esgotado: força a finalização em vez de
            # deixar o modelo tentar mais buscar_web (que só voltaria vazio)
            response_parts.append(types.Part.from_text(
                text="Você já usou todas as buscas disponíveis. Chame registrar_resultado agora com o que apurou."
            ))
            ja_pediu_para_finalizar = True

        contents.append(types.Content(role="user", parts=response_parts))

    return Enrichment(
        lead_id=lead_id,
        site_oficial="",
        telefone_comercial_ia="",
        whatsapp_publico="",
        linkedin_decisor="",
        telefone_decisor_ia="",
        fonte_ia="",
        confianca_ia="baixa",
        buscas_realizadas=" | ".join(buscas_feitas),
    )


def main(
    input_path: str,
    output_path: str,
    max_searches: int = MAX_SEARCHES_PADRAO,
    pular_se_preenchido: list[str] | None = None,
) -> None:
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

    fieldnames = ["lead_id", "site_oficial", "telefone_comercial_ia", "whatsapp_publico", "linkedin_decisor", "telefone_decisor_ia", "fonte_ia", "confianca_ia", "buscas_realizadas"]
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

            if pular_se_preenchido and any(lead.get(campo, "").strip() for campo in pular_se_preenchido):
                print(f"[{i}/{len(leads)}] pulando {nome_exibicao} (já tem contato na base)...", file=sys.stderr, flush=True)
                pulado = Enrichment(
                    lead_id=current_id, site_oficial="", telefone_comercial_ia="",
                    whatsapp_publico="", linkedin_decisor="", telefone_decisor_ia="",
                    fonte_ia="", confianca_ia="pulado_ja_tinha_contato",
                    buscas_realizadas="",
                )
                writer.writerow(asdict(pulado))
                f_out.flush()
                continue

            print(f"[{i}/{len(leads)}] investigando {nome_exibicao}...", file=sys.stderr, flush=True)
            enrichment = enrich_one(client, brave_api_key, lead, max_searches)
            writer.writerow(asdict(enrichment))
            f_out.flush()
            time.sleep(SECONDS_BETWEEN_LEADS)

    print("CONCLUIDO", file=sys.stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Enriquece leads com site/telefone/WhatsApp via agente de busca ativa (Brave Search + Gemini function calling).")
    parser.add_argument("entrada", help="CSV de entrada")
    parser.add_argument("saida", help="CSV de saída (retomável: pula ids já presentes)")
    parser.add_argument("max_buscas", nargs="?", type=int, default=MAX_SEARCHES_PADRAO, help=f"Orçamento de buscas por empresa (padrão {MAX_SEARCHES_PADRAO})")
    parser.add_argument(
        "--pular-com-contato",
        metavar="COL1,COL2,...",
        default=None,
        help="Colunas do CSV de entrada que, se já preenchidas, pulam o lead sem gastar chamadas (mesmo uso do enrich_leads.py).",
    )
    args = parser.parse_args()

    pular = [c.strip() for c in args.pular_com_contato.split(",")] if args.pular_com_contato else None
    main(args.entrada, args.saida, args.max_buscas, pular_se_preenchido=pular)
