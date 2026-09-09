"""
Enriquece leads com site/telefone/WhatsApp/LinkedIn do decisor usando SÓ o
Brave Search (plano gratuito, sem cartão) + heurísticas de texto (regex,
comparação de domínio) — SEM NENHUMA chamada de LLM. Custo real zero,
dentro da cota gratuita do Brave.

Trade-off: sem um modelo de IA julgando os resultados, a precisão é menor
que enrich_leads.py ou agentic_enrich.py — trate isso como uma primeira
triagem gratuita da base inteira. Para os leads mais importantes, ainda
vale rodar depois a versão com IA (mais cara, mas mais precisa) só nesse
subconjunto menor.

Requisitos: só BRAVE_API_KEY (https://brave.com/search/api/, free tier).
Não precisa de GEMINI_API_KEY nem de billing em lugar nenhum.

Uso:
    export BRAVE_API_KEY="sua-chave"
    python enrich_leads_gratis.py leads_com_socios.csv leads_enriquecidos.csv

Roda de novo em cima do mesmo arquivo de saída para retomar de onde parou.
"""

import csv
import os
import re
import sys
import time
import unicodedata

import requests

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
SECONDS_BETWEEN_CALLS = 1.0

# Domínios que não contam como "site oficial" mesmo aparecendo no topo da
# busca — diretórios, redes sociais e agregadores de CNPJ, não o site da
# própria empresa.
DOMINIOS_IGNORADOS = {
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "mercadolivre.com.br", "reclameaqui.com.br",
    "glassdoor.com.br", "glassdoor.com", "indeed.com", "econodata.com.br",
    "cnpj.biz", "empresascnpj.com", "consultasocio.com", "cnpja.com",
    "casadosdados.com.br", "solutudo.com.br", "google.com", "bing.com",
    "wikipedia.org", "apple.com", "play.google.com", "todosnegocios.com",
    "cnpj.info", "consultacnpj.com", "cnpjs.com.br", "guiamais.com.br",
    "telelistas.net", "listatelefone.com.br", "econodata.com",
}

# Domínios cujo próprio NOME sugere serviço de espionagem/rastreamento de
# terceiros (apps de "espião de celular", etc.) — nunca são fonte válida,
# mesmo que não estejam na lista acima.
PADRAO_DOMINIO_SUSPEITO = re.compile(r"espi[ao]|rastre|spy|stalk", re.IGNORECASE)


def dominio_bloqueado(url: str) -> bool:
    dominio = dominio_de(url)
    if not dominio:
        return True
    if any(dominio == d or dominio.endswith("." + d) for d in DOMINIOS_IGNORADOS):
        return True
    if PADRAO_DOMINIO_SUSPEITO.search(url):
        return True
    # Regra geral: nenhuma empresa de verdade usa "cnpj" no próprio domínio —
    # isso é sempre um site agregador/consulta (existem dezenas, impossível
    # listar todos um por um), então bloqueia por padrão.
    if "cnpj" in dominio:
        return True
    return False

REGEX_TELEFONE = re.compile(r"(?:\+?55\s?)?\(?\d{2}\)?[\s.-]?\d{4,5}[\s.-]?\d{4}")


def normaliza(texto: str) -> str:
    texto = texto or ""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", texto.lower())


SUFIXOS_EMPRESA = ["ltda", "sa", "eireli", "me", "epp", "s/a", "s.a.", "inovasimples", "is"]


def nome_base(razao_social: str) -> str:
    t = normaliza(razao_social)
    for sufixo in SUFIXOS_EMPRESA:
        s = normaliza(sufixo)
        if t.endswith(s):
            t = t[: -len(s)]
    return t


def dominio_de(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1).lower() if m else ""


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
            {"titulo": item.get("title", ""), "url": item.get("url", ""), "resumo": item.get("description", "")}
        )
    return resultados


def achar_site_oficial(resultados: list[dict], razao_social: str, nome_fantasia: str) -> tuple[str, str]:
    alvo_razao = nome_base(razao_social)
    alvo_fantasia = nome_base(nome_fantasia) if nome_fantasia else ""
    for r in resultados:
        if dominio_bloqueado(r["url"]):
            continue
        dominio_norm = normaliza(dominio_de(r["url"]).split(".")[0])
        if (alvo_fantasia and alvo_fantasia in dominio_norm) or (alvo_razao and alvo_razao[:8] in dominio_norm):
            return r["url"], r["url"]
    return "", ""


def achar_telefone(resultados: list[dict], cnpj: str, razao_social: str, nome_fantasia: str) -> tuple[str, str]:
    """Devolve (telefone, fonte_url). Ignora resultados de sites de
    consulta/diretório/spyware, qualquer número que na verdade seja o
    próprio CNPJ (janela dele capturada por engano pelo regex), e qualquer
    resultado cujo texto não mencione a empresa — sem essa trava, uma busca
    genérica pode "achar" um telefone em conteúdo completamente aleatório
    (spam, post de rede social) que só coincide no formato do número."""
    cnpj_digitos = re.sub(r"\D", "", cnpj or "")
    alvo_razao = nome_base(razao_social)
    alvo_fantasia = nome_base(nome_fantasia) if nome_fantasia else ""
    for r in resultados:
        if dominio_bloqueado(r["url"]):
            continue
        texto = f"{r['titulo']} {r['resumo']}"
        texto_norm = normaliza(texto)
        menciona_empresa = (alvo_razao and alvo_razao[:8] in texto_norm) or (alvo_fantasia and alvo_fantasia in texto_norm)
        if not menciona_empresa:
            continue
        for m in REGEX_TELEFONE.finditer(texto):
            digitos = re.sub(r"\D", "", m.group(0))
            if cnpj_digitos and (digitos in cnpj_digitos or cnpj_digitos.startswith(digitos[:8])):
                continue  # provavelmente o próprio CNPJ, não um telefone
            return digitos, r["url"]
    return "", ""


def achar_linkedin(resultados: list[dict], nome_decisor: str) -> str:
    """Só aceita um perfil do LinkedIn se algum sobrenome do decisor aparecer
    na própria URL/título do resultado — sem isso, a busca pode devolver o
    perfil de outra pessoa qualquer com nome parecido."""
    partes_nome = [normaliza(p) for p in nome_decisor.split() if len(p) > 2]
    for r in resultados:
        if "linkedin.com/in/" not in r["url"] or PADRAO_DOMINIO_SUSPEITO.search(r["url"]):
            continue
        alvo = normaliza(r["url"] + " " + r["titulo"])
        if any(parte in alvo for parte in partes_nome):
            return r["url"]
    return ""


def enrich_one(lead: dict, brave_api_key: str) -> dict:
    razao_social = lead.get("razao_social", "")
    nome_fantasia = lead.get("nome_fantasia", "")
    municipio = lead.get("municipio", "")
    uf = lead.get("uf", "")
    nome_decisor = (lead.get("nome_decisor") or "").strip()

    fontes = []
    site_oficial = telefone_comercial_ia = whatsapp_publico = ""
    linkedin_decisor = ""

    try:
        r1 = brave_search(f"{razao_social} {municipio} {uf} site oficial", brave_api_key)
    except Exception as exc:
        print(f"[aviso] busca de site falhou p/ {razao_social}: {exc}", file=sys.stderr)
        r1 = []
    site_oficial, fonte_site = achar_site_oficial(r1, razao_social, nome_fantasia)
    if fonte_site:
        fontes.append(fonte_site)

    time.sleep(SECONDS_BETWEEN_CALLS)
    try:
        r2 = brave_search(f"{razao_social} telefone whatsapp contato", brave_api_key)
    except Exception as exc:
        print(f"[aviso] busca de telefone falhou p/ {razao_social}: {exc}", file=sys.stderr)
        r2 = []
    telefone_comercial_ia, fonte_tel = achar_telefone(r2, lead.get("cnpj", ""), razao_social, nome_fantasia)
    if fonte_tel:
        fontes.append(fonte_tel)
    if telefone_comercial_ia and any("whatsapp" in (r["titulo"] + r["resumo"]).lower() for r in r2):
        whatsapp_publico = telefone_comercial_ia

    if nome_decisor:
        time.sleep(SECONDS_BETWEEN_CALLS)
        try:
            r3 = brave_search(f"{nome_decisor} linkedin {razao_social}", brave_api_key)
        except Exception as exc:
            print(f"[aviso] busca de linkedin falhou p/ {nome_decisor}: {exc}", file=sys.stderr)
            r3 = []
        linkedin_decisor = achar_linkedin(r3, nome_decisor)
        if linkedin_decisor:
            fontes.append(linkedin_decisor)

    confianca = "media" if site_oficial else "baixa"

    return {
        "cnpj": lead.get("cnpj", ""),
        "site_oficial": site_oficial,
        "telefone_comercial_ia": telefone_comercial_ia,
        "whatsapp_publico": whatsapp_publico,
        "linkedin_decisor": linkedin_decisor,
        "telefone_decisor_ia": "",
        "fonte_ia": " | ".join(dict.fromkeys(fontes)),
        "confianca_ia": confianca,
    }


def main(input_path: str, output_path: str) -> None:
    brave_api_key = os.environ["BRAVE_API_KEY"]

    with open(input_path, newline="", encoding="utf-8") as f_in:
        leads = list(csv.DictReader(f_in))
    if not leads:
        print("Nenhum lead no CSV de entrada.", file=sys.stderr)
        return

    campos_saida = [
        "cnpj", "site_oficial", "telefone_comercial_ia", "whatsapp_publico",
        "linkedin_decisor", "telefone_decisor_ia", "fonte_ia", "confianca_ia",
    ]

    done_cnpjs = set()
    if os.path.exists(output_path):
        with open(output_path, newline="", encoding="utf-8") as f_prev:
            for row in csv.DictReader(f_prev):
                done_cnpjs.add(row["cnpj"])

    write_header = not os.path.exists(output_path)
    with open(output_path, "a", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=campos_saida)
        if write_header:
            writer.writeheader()

        for i, lead in enumerate(leads, start=1):
            cnpj = lead.get("cnpj", "")
            if cnpj and cnpj in done_cnpjs:
                continue
            print(f"[{i}/{len(leads)}] pesquisando {lead.get('razao_social')} (gratuito, Brave)...", file=sys.stderr)
            resultado = enrich_one(lead, brave_api_key)
            writer.writerow(resultado)
            f_out.flush()
            time.sleep(SECONDS_BETWEEN_CALLS)

    print("CONCLUIDO", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Uso: python enrich_leads_gratis.py <entrada.csv> <saida.csv>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
