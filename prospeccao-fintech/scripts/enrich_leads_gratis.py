"""
Enriquece leads com site/telefone/WhatsApp/LinkedIn do decisor usando SÓ o
Brave Search + heurísticas de texto (regex, comparação de domínio) — SEM
NENHUMA chamada de LLM. Custo bem baixo (o Brave cobra por busca desde
fev/2026, não é mais 100% gratuito — mas é uma fração do custo de usar IA).

Filtro de porte (--max-funcionarios): busca a página da empresa no LinkedIn
e extrai a faixa "X-Y employees" que o Brave mostra no resumo. Se a faixa
inteira estiver acima do limite, pula o resto das buscas desse lead pra não
gastar à toa com quem não interessa — é o PRIMEIRO passo de cada lead. As
faixas do LinkedIn (1-10, 11-50, 51-200...) não batem exatamente com
qualquer limite arbitrário, então quando o limite cai DENTRO de uma faixa
(ex.: limite 40 dentro de "11-50"), o lead passa mesmo assim (falso negativo
é pior que falso positivo aqui) — a coluna `porte_linkedin` mostra a faixa
real encontrada pra você conferir manualmente se quiser.

Trade-off: sem um modelo de IA julgando os resultados, a precisão é menor
que enrich_leads.py ou agentic_enrich.py — trate isso como uma primeira
triagem gratuita da base inteira. Para os leads mais importantes, ainda
vale rodar depois a versão com IA (mais cara, mas mais precisa) só nesse
subconjunto menor.

Além do site/telefone/WhatsApp da empresa e do LinkedIn do decisor, quando
há um nome de decisor no lead também busca um link wa.me/api.whatsapp.com
associado a essa pessoa (bio de Instagram, Linktree, site pessoal) — é o
sinal mais confiável de WhatsApp pessoal genuíno que dá pra achar sem IA,
mas ainda assim raro de existir. Isso usa 1 busca a mais por lead com
decisor (4 no total em vez de 3), então consome a cota gratuita do Brave
mais rápido — ajuste o volume por rodada se estiver perto do limite mensal.

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

# "Company size · 11-50 employees" (ou "51-200 employees", etc.) — é assim
# que o LinkedIn expõe a faixa de funcionários, e o Brave costuma indexar
# esse texto no resumo da página da empresa quando ela aparece nos resultados.
REGEX_FUNCIONARIOS = re.compile(r"(\d[\d,]*)\s*-\s*(\d[\d,]*)\s*employees", re.IGNORECASE)


def achar_porte_empresa(resultados: list[dict], razao_social: str, nome_fantasia: str) -> tuple[str, str]:
    """Devolve (faixa_encontrada, classificacao), onde classificacao é
    'sim' (toda a faixa está dentro do limite), 'nao' (toda a faixa está
    acima), 'provavel' (o limite cai dentro da faixa) ou '' (não achou)."""
    alvo_razao = nome_base(razao_social)
    alvo_fantasia = nome_base(nome_fantasia) if nome_fantasia else ""
    for r in resultados:
        if "linkedin.com/company/" not in r["url"]:
            continue
        alvo = normaliza(r["url"] + " " + r["titulo"])
        menciona_empresa = (alvo_fantasia and alvo_fantasia in alvo) or (alvo_razao and alvo_razao[:6] in alvo)
        if not menciona_empresa:
            continue
        m = REGEX_FUNCIONARIOS.search(r["resumo"])
        if not m:
            continue
        baixo = int(m.group(1).replace(",", ""))
        alto = int(m.group(2).replace(",", ""))
        return f"{baixo}-{alto}", (baixo, alto)
    return "", None

# Link direto de WhatsApp (wa.me/<numero> ou api.whatsapp.com/send?phone=<numero>).
# Esse formato só existe quando ALGUÉM monta deliberadamente um link clicável de
# contato — é o sinal mais forte de número pessoal genuinamente publicado pela
# própria pessoa (bio de Instagram, Linktree, site pessoal de um único produto),
# bem mais confiável que um número solto encontrado em texto livre.
REGEX_WHATSAPP_LINK = re.compile(r"(?:wa\.me/\+?|api\.whatsapp\.com/send\?phone=\+?)(\d{10,13})")


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


def achar_whatsapp_pessoal(resultados: list[dict], nome_decisor: str) -> tuple[str, str]:
    """Procura um link wa.me/api.whatsapp.com nos resultados — na URL do
    próprio resultado (ex.: um Linktree cujo link de destino é um wa.me) ou
    no título/resumo (páginas que exibem o link como texto). Exige que o
    resultado mencione o nome do decisor, mesma lógica de achar_telefone."""
    partes_nome = [p for p in nome_decisor.split() if len(p) > 2]
    for r in resultados:
        if dominio_bloqueado(r["url"]):
            continue
        alvo = normaliza(r["url"] + " " + r["titulo"] + " " + r["resumo"])
        menciona_pessoa = any(normaliza(p) in alvo for p in partes_nome)
        if not menciona_pessoa:
            continue
        for campo in (r["url"], r["titulo"], r["resumo"]):
            m = REGEX_WHATSAPP_LINK.search(campo)
            if m:
                return m.group(1), r["url"]
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


def enrich_one(lead: dict, brave_api_key: str, max_funcionarios: int | None = None) -> dict:
    razao_social = lead.get("razao_social", "")
    nome_fantasia = lead.get("nome_fantasia", "")
    municipio = lead.get("municipio", "")
    uf = lead.get("uf", "")
    nome_decisor = (lead.get("nome_decisor") or "").strip()

    fontes = []
    site_oficial = telefone_comercial_ia = whatsapp_publico = ""
    linkedin_decisor = ""
    porte_linkedin = ""
    porte_classificacao = ""

    if max_funcionarios is not None:
        try:
            r0 = brave_search(f"{razao_social} linkedin funcionarios employees", brave_api_key)
        except Exception as exc:
            print(f"[aviso] busca de porte falhou p/ {razao_social}: {exc}", file=sys.stderr)
            r0 = []
        porte_linkedin, faixa = achar_porte_empresa(r0, razao_social, nome_fantasia)
        if faixa:
            baixo, alto = faixa
            if alto <= max_funcionarios:
                porte_classificacao = "sim"
            elif baixo > max_funcionarios:
                porte_classificacao = "nao"
            else:
                porte_classificacao = "provavel"  # limite cai dentro da faixa

        if porte_classificacao == "nao":
            # empresa claramente maior que o limite: não vale gastar o
            # resto das buscas (site/telefone/decisor) nela
            return {
                "cnpj": lead.get("cnpj", ""),
                "site_oficial": "",
                "telefone_comercial_ia": "",
                "whatsapp_publico": "",
                "linkedin_decisor": "",
                "telefone_decisor_ia": "",
                "porte_linkedin": porte_linkedin,
                "porte_ate_limite": porte_classificacao,
                "verificacao": "pulado_porte_acima_do_limite",
                "fonte_ia": "",
                "confianca_ia": "baixa",
            }
        time.sleep(SECONDS_BETWEEN_CALLS)

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

    telefone_decisor_ia = ""
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

        time.sleep(SECONDS_BETWEEN_CALLS)
        try:
            r4 = brave_search(f'"{nome_decisor}" wa.me', brave_api_key)
        except Exception as exc:
            print(f"[aviso] busca de wa.me falhou p/ {nome_decisor}: {exc}", file=sys.stderr)
            r4 = []
        telefone_decisor_ia, fonte_wa = achar_whatsapp_pessoal(r4, nome_decisor)
        if fonte_wa:
            fontes.append(fonte_wa)

    # Cruzamento de fontes, sem custo e sem serviço de terceiros: se o
    # telefone achado especificamente pelo NOME da pessoa (telefone_decisor_ia,
    # via wa.me) bate com o telefone/WhatsApp achado pela busca da EMPRESA,
    # isso é evidência real de que é a mesma pessoa — apareceu de forma
    # independente em dois contextos diferentes, não é só um número solto.
    numero_bate = bool(telefone_decisor_ia) and telefone_decisor_ia in (telefone_comercial_ia, whatsapp_publico)
    verificacao = "numero_pessoal_bate_com_comercial" if numero_bate else ("so_pessoal" if telefone_decisor_ia else "so_comercial")

    if numero_bate:
        confianca = "alta"
    elif site_oficial:
        confianca = "media"
    else:
        confianca = "baixa"

    return {
        "cnpj": lead.get("cnpj", ""),
        "site_oficial": site_oficial,
        "telefone_comercial_ia": telefone_comercial_ia,
        "whatsapp_publico": whatsapp_publico,
        "linkedin_decisor": linkedin_decisor,
        "telefone_decisor_ia": telefone_decisor_ia,
        "porte_linkedin": porte_linkedin,
        "porte_ate_limite": porte_classificacao,
        "verificacao": verificacao,
        "fonte_ia": " | ".join(dict.fromkeys(fontes)),
        "confianca_ia": confianca,
    }


def main(input_path: str, output_path: str, max_funcionarios: int | None = None) -> None:
    brave_api_key = os.environ["BRAVE_API_KEY"]

    with open(input_path, newline="", encoding="utf-8") as f_in:
        leads = list(csv.DictReader(f_in))
    if not leads:
        print("Nenhum lead no CSV de entrada.", file=sys.stderr)
        return

    campos_saida = [
        "cnpj", "site_oficial", "telefone_comercial_ia", "whatsapp_publico",
        "linkedin_decisor", "telefone_decisor_ia", "porte_linkedin", "porte_ate_limite",
        "verificacao", "fonte_ia", "confianca_ia",
    ]

    done_cnpjs = set()
    if os.path.exists(output_path):
        with open(output_path, newline="", encoding="utf-8") as f_prev:
            for row in csv.DictReader(f_prev):
                done_cnpjs.add(row["cnpj"])

    write_header = not os.path.exists(output_path)
    pulados_por_porte = 0
    with open(output_path, "a", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=campos_saida)
        if write_header:
            writer.writeheader()

        for i, lead in enumerate(leads, start=1):
            cnpj = lead.get("cnpj", "")
            if cnpj and cnpj in done_cnpjs:
                continue
            print(f"[{i}/{len(leads)}] pesquisando {lead.get('razao_social')} (gratuito, Brave)...", file=sys.stderr)
            resultado = enrich_one(lead, brave_api_key, max_funcionarios)
            if resultado.get("verificacao") == "pulado_porte_acima_do_limite":
                pulados_por_porte += 1
            writer.writerow(resultado)
            f_out.flush()
            time.sleep(SECONDS_BETWEEN_CALLS)

    if max_funcionarios is not None:
        print(f"[info] {pulados_por_porte} leads pulados por terem mais de {max_funcionarios} funcionários (LinkedIn)", file=sys.stderr)
    print("CONCLUIDO", file=sys.stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Enriquece leads via Brave Search (sem LLM). Custo baixo, não é mais 100% gratuito desde fev/2026.")
    parser.add_argument("entrada", help="CSV de entrada")
    parser.add_argument("saida", help="CSV de saída (retomável)")
    parser.add_argument(
        "--max-funcionarios",
        type=int,
        default=None,
        help="Se informado, checa o porte da empresa no LinkedIn primeiro e pula o resto das buscas para empresas claramente acima desse limite (ex.: --max-funcionarios 40)",
    )
    args = parser.parse_args()
    main(args.entrada, args.saida, args.max_funcionarios)
