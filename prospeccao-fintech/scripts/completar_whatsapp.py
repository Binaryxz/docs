"""
Fecha a lacuna real do pipeline: a maioria dos telefones da Receita Federal
não tem WhatsApp (número de fixo, número antigo, número do contador). Este
script:

  1. Checa se o telefone que já temos tem WhatsApp (via servidor_whatsapp.js,
     que precisa estar rodando — ver prospeccao-fintech/scripts/whatsapp-checker/).
  2. Se NÃO tiver, busca na internet (Brave Search) um telefone alternativo
     ligado ao NOME DO DECISOR — link direto de WhatsApp (wa.me/api.whatsapp.com)
     em bio de Instagram/Linktree/site pessoal, ou telefone comercial da
     empresa mencionando o decisor.
  3. Checa WhatsApp nesse número alternativo também.
  4. Marca o resultado final e, com --somente-com-whatsapp, filtra a saída
     pra só quem tem WhatsApp confirmado em algum dos dois — é o "fazer o
     filtro" que fecha o funil de verdade.

Pré-requisitos:
  1. Rodar `node servidor_whatsapp.js` em prospeccao-fintech/scripts/whatsapp-checker/
     (escanear o QR code lá, com um chip secundário — mesmos avisos de sempre)
     e deixar rodando.
  2. export BRAVE_API_KEY="sua-chave"

Uso:
    python completar_whatsapp.py leads.csv leads_com_whatsapp.csv \
      --coluna-telefone telefone_e164 --somente-com-whatsapp

Retomável: pula CNPJ que já está no CSV de saída.
"""

import argparse
import csv
import os
import re
import sys
import time
import unicodedata
import urllib.parse

import requests

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
SECONDS_BETWEEN_BRAVE_CALLS = 1.0

REGEX_WHATSAPP_LINK = re.compile(r"(?:wa\.me/\+?|api\.whatsapp\.com/send\?phone=\+?)(\d{10,13})")
REGEX_TELEFONE = re.compile(r"(?:\+?55\s?)?\(?\d{2}\)?[\s.-]?\d{4,5}[\s.-]?\d{4}")

DOMINIOS_IGNORADOS = {
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "mercadolivre.com.br", "reclameaqui.com.br",
    "glassdoor.com.br", "glassdoor.com", "indeed.com", "econodata.com.br",
    "cnpj.biz", "empresascnpj.com", "consultasocio.com", "cnpja.com",
    "casadosdados.com.br", "solutudo.com.br", "google.com", "bing.com",
}


def normaliza(texto: str) -> str:
    texto = texto or ""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def dominio_de(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1).lower() if m else ""


def dominio_bloqueado(url: str) -> bool:
    dominio = dominio_de(url)
    if not dominio:
        return True
    return any(dominio == d or dominio.endswith("." + d) for d in DOMINIOS_IGNORADOS)


def normaliza_para_e164(numero_bruto: str) -> str:
    """Heurística simples (mesma ideia de validar_telefone.py): garante DDI 55
    e corrige celular pré-2012 sem o 9º dígito. Não valida estrutura completa
    (isso já foi feito antes, na Etapa 2.6) — só normaliza formato."""
    digitos = re.sub(r"\D", "", numero_bruto or "")
    if not digitos:
        return ""
    if digitos.startswith("55") and len(digitos) in (12, 13):
        pass
    elif len(digitos) in (10, 11):
        digitos = "55" + digitos
    else:
        return ""
    # 55 + DDD(2) + local(8, pré-2012) -> insere o 9
    if len(digitos) == 12 and digitos[4] in "6789":
        digitos = digitos[:4] + "9" + digitos[4:]
    return digitos


def brave_search(query: str, api_key: str, count: int = 5) -> list[dict]:
    resp = requests.get(
        BRAVE_ENDPOINT,
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        params={"q": query, "count": count},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        {"titulo": i.get("title", ""), "url": i.get("url", ""), "resumo": i.get("description", "")}
        for i in data.get("web", {}).get("results", [])[:count]
    ]


def candidatos_whatsapp_pessoal(resultados: list[dict], nome_decisor: str) -> list[str]:
    partes_nome = [normaliza(p) for p in nome_decisor.split() if len(p) > 2]
    candidatos = []
    for r in resultados:
        if dominio_bloqueado(r["url"]):
            continue
        alvo = normaliza(r["url"] + " " + r["titulo"] + " " + r["resumo"])
        if not any(p in alvo for p in partes_nome):
            continue
        for campo in (r["url"], r["titulo"], r["resumo"]):
            m = REGEX_WHATSAPP_LINK.search(campo)
            if m:
                candidatos.append(m.group(1))
    return candidatos


def candidatos_telefone_comercial(resultados: list[dict], razao_social: str, cnpj: str) -> list[str]:
    cnpj_digitos = re.sub(r"\D", "", cnpj or "")
    alvo_razao = normaliza(razao_social)[:8]
    candidatos = []
    for r in resultados:
        if dominio_bloqueado(r["url"]):
            continue
        texto = f"{r['titulo']} {r['resumo']}"
        if alvo_razao and alvo_razao not in normaliza(texto):
            continue
        for m in REGEX_TELEFONE.finditer(texto):
            digitos = re.sub(r"\D", "", m.group(0))
            if cnpj_digitos and (digitos in cnpj_digitos or cnpj_digitos.startswith(digitos[:8])):
                continue
            candidatos.append(digitos)
    return candidatos


def checa_whatsapp(servidor_url: str, numero_e164: str) -> dict:
    if not numero_e164:
        return {"tem_whatsapp": "sem_numero", "whatsapp_jid": "", "erro": ""}
    try:
        resp = requests.get(
            f"{servidor_url}/verificar",
            params={"numero": numero_e164},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        return {"tem_whatsapp": "erro", "whatsapp_jid": "", "erro": f"servidor_whatsapp_inacessivel: {exc}"}


def processa_lead(lead: dict, coluna_telefone: str, servidor_url: str, brave_api_key: str | None) -> dict:
    telefone_original = normaliza_para_e164(lead.get(coluna_telefone, ""))
    r_original = checa_whatsapp(servidor_url, telefone_original)

    resultado = {
        "cnpj": lead.get("cnpj", ""),
        "telefone_original": telefone_original,
        "tem_whatsapp_original": r_original["tem_whatsapp"],
        "telefone_alternativo": "",
        "fonte_alternativo": "",
        "tem_whatsapp_alternativo": "",
        "telefone_final": "",
        "tem_whatsapp_final": "nao",
    }

    if r_original["tem_whatsapp"] == "sim":
        resultado["telefone_final"] = telefone_original
        resultado["tem_whatsapp_final"] = "sim"
        return resultado

    nome_decisor = (lead.get("nome_decisor") or "").strip()
    razao_social = lead.get("razao_social", "")
    if not brave_api_key or (not nome_decisor and not razao_social):
        return resultado

    candidatos: list[tuple[str, str]] = []  # (numero_bruto, fonte)

    if nome_decisor:
        try:
            r1 = brave_search(f'"{nome_decisor}" wa.me', brave_api_key)
            time.sleep(SECONDS_BETWEEN_BRAVE_CALLS)
        except Exception as exc:
            print(f"[aviso] busca wa.me falhou p/ {nome_decisor}: {exc}", file=sys.stderr)
            r1 = []
        for numero in candidatos_whatsapp_pessoal(r1, nome_decisor):
            candidatos.append((numero, "wa.me_pessoal"))

    if razao_social:
        try:
            r2 = brave_search(f"{razao_social} telefone whatsapp contato", brave_api_key)
            time.sleep(SECONDS_BETWEEN_BRAVE_CALLS)
        except Exception as exc:
            print(f"[aviso] busca telefone falhou p/ {razao_social}: {exc}", file=sys.stderr)
            r2 = []
        for numero in candidatos_telefone_comercial(r2, razao_social, lead.get("cnpj", "")):
            candidatos.append((numero, "site_comercial"))

    vistos = set()
    for numero_bruto, fonte in candidatos:
        numero_e164 = normaliza_para_e164(numero_bruto)
        if not numero_e164 or numero_e164 in vistos or numero_e164 == telefone_original:
            continue
        vistos.add(numero_e164)

        r_alt = checa_whatsapp(servidor_url, numero_e164)
        if r_alt["tem_whatsapp"] == "sim":
            resultado["telefone_alternativo"] = numero_e164
            resultado["fonte_alternativo"] = fonte
            resultado["tem_whatsapp_alternativo"] = "sim"
            resultado["telefone_final"] = numero_e164
            resultado["tem_whatsapp_final"] = "sim"
            return resultado

    # nenhum candidato teve whatsapp — registra o primeiro tentado, pra
    # conferência manual, mas o filtro final continua "nao"
    if candidatos:
        primeiro = normaliza_para_e164(candidatos[0][0])
        resultado["telefone_alternativo"] = primeiro
        resultado["fonte_alternativo"] = candidatos[0][1]
        resultado["tem_whatsapp_alternativo"] = "nao"

    return resultado


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", help="CSV de entrada (precisa de cnpj, telefone, razao_social; nome_decisor opcional)")
    parser.add_argument("saida", help="CSV de saída (retomável)")
    parser.add_argument("--coluna-telefone", default="telefone_e164", help="Coluna com o telefone original (padrão telefone_e164)")
    parser.add_argument("--servidor-whatsapp", default="http://localhost:3939", help="URL do servidor_whatsapp.js (padrão http://localhost:3939)")
    parser.add_argument("--somente-com-whatsapp", action="store_true", help="Grava no CSV de saída só quem ficou com tem_whatsapp_final=sim")
    args = parser.parse_args()

    brave_api_key = os.environ.get("BRAVE_API_KEY")
    if not brave_api_key:
        print("[aviso] BRAVE_API_KEY não definida — não vai buscar número alternativo pra quem não tem WhatsApp no original.", file=sys.stderr)

    try:
        saude = requests.get(f"{args.servidor_whatsapp}/health", timeout=5).json()
        if not saude.get("conectado"):
            print(f"[aviso] {args.servidor_whatsapp}/health respondeu mas a sessão do WhatsApp não está conectada — escaneie o QR code do servidor_whatsapp.js primeiro.", file=sys.stderr)
    except requests.RequestException:
        print(f"[erro] não consegui acessar {args.servidor_whatsapp} — rode `node servidor_whatsapp.js` primeiro (em prospeccao-fintech/scripts/whatsapp-checker/).", file=sys.stderr)
        sys.exit(1)

    with open(args.entrada, newline="", encoding="utf-8") as f_in:
        leads = list(csv.DictReader(f_in))
    if not leads:
        print("Nenhum lead no CSV de entrada.", file=sys.stderr)
        return

    campos_saida = list(leads[0].keys()) + [
        "telefone_original", "tem_whatsapp_original", "telefone_alternativo",
        "fonte_alternativo", "tem_whatsapp_alternativo", "telefone_final", "tem_whatsapp_final",
    ]

    done_cnpjs = set()
    if os.path.exists(args.saida):
        with open(args.saida, newline="", encoding="utf-8") as f_prev:
            for row in csv.DictReader(f_prev):
                done_cnpjs.add(row.get("cnpj", ""))

    write_header = not os.path.exists(args.saida)
    contagem = {"sim": 0, "nao": 0}

    with open(args.saida, "a", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=campos_saida)
        if write_header:
            writer.writeheader()

        for i, lead in enumerate(leads, start=1):
            cnpj = lead.get("cnpj", "")
            if cnpj and cnpj in done_cnpjs:
                continue
            print(f"[{i}/{len(leads)}] {lead.get('razao_social', '')}...", file=sys.stderr)
            resultado = processa_lead(lead, args.coluna_telefone, args.servidor_whatsapp, brave_api_key)
            contagem[resultado["tem_whatsapp_final"]] = contagem.get(resultado["tem_whatsapp_final"], 0) + 1

            if args.somente_com_whatsapp and resultado["tem_whatsapp_final"] != "sim":
                continue
            writer.writerow({**lead, **resultado})
            f_out.flush()

    print(f"\nResumo: {contagem}", file=sys.stderr)
    print(f"Gravado em {args.saida}", file=sys.stderr)


if __name__ == "__main__":
    main()
