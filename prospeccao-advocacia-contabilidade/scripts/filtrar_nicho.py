"""
Sub-filtro de NICHO em cima de uma lista de leads já filtrada por segmento
(saída de filtrar_cnpjs_segmento.py, ou de preparar_telefones.py). Não relê a
base da Receita: só reclassifica os leads que já passaram pelo CNAE.

Nichos (NICHOS abaixo), cada um com sub-nichos (tags):

  advocacia_patrimonial  (base: --segmento advocacia)
    tributario · societario · familia_sucessoes · patrimonial
  contabil_digital       (base: --segmento contabil)
    digital · infoprodutores · exportacao_cambio · traders_cripto

Onde procura:
  - razão social e nome fantasia: termo como PALAVRA inteira (mesma regra do
    filtro de segmento; "XYZ*" é prefixo);
  - e-mail (antes e depois do @): como SUBSTRING, só para termos com 6+
    letras — "contabilidadedigital.com.br" casa DIGITAL, mas "web"/"app" não
    viram falso positivo dentro de outras palavras;
  - CNAEs secundários do nicho (cnaes_sinal): contam como sinal; com
    --aceitar-cnae, entram mesmo sem keyword (só sub-nicho "cnae").

Keyword negativa do nicho (em razão social, fantasia ou e-mail) tira o lead.

Colunas novas: nicho, nicho_tags (sub-nichos que bateram, separados por |),
nicho_termos (termos que bateram), nicho_fonte (nome/email/cnae), nicho_score
(nº de sub-nichos + 1 se o termo veio do nome + 1 por CNAE-sinal). A saída sai
ordenada por nicho_score (desc), mantendo a ordem anterior no empate.

Uso:
    python filtrar_nicho.py --nicho advocacia_patrimonial leads_advocacia_tel.csv nicho_advocacia.csv
    python filtrar_nicho.py --nicho contabil_digital leads_contabil_tel.csv nicho_contabil.csv [--aceitar-cnae]
"""

import argparse
import csv
import re
import sys

from filtrar_cnpjs_segmento import compila_termos, normaliza, termos_encontrados

csv.field_size_limit(sys.maxsize)

NICHOS = {
    "advocacia_patrimonial": {
        "tags": {
            "tributario": [
                "TRIBUTARI*", "FISCAL", "FISCAIS", "TAX", "IMPOSTO*", "ICMS", "RECUPERACAO TRIBUTARIA",
                "PLANEJAMENTO TRIBUTARIO", "CONTENCIOSO",
            ],
            "societario": [
                "SOCIETARI*", "EMPRESARIA*", "CORPORAT*", "CORPORATE", "FUSOES", "AQUISICOES", "M&A",
                "GOVERNANCA", "STARTUP*", "VENTURE", "NEGOCIOS", "BUSINESS", "COMPLIANCE", "CONTRATOS",
            ],
            "familia_sucessoes": [
                "FAMILIA", "FAMILIAR", "FAMILY", "SUCESSOES", "SUCESSORI*", "INVENTARIO*", "HERANCA*",
                "TESTAMENTO*", "DIVORCIO*",
            ],
            "patrimonial": [
                "PATRIMONI*", "HOLDING*", "BLINDAGEM", "WEALTH", "FAMILY OFFICE", "PRIVATE", "ESTATE",
                "TRUST*", "OFFSHORE", "ASSET*", "BOUTIQUE", "PLANEJAMENTO",
            ],
        },
        "negativas": [
            "CRIMINAL", "CRIMINAIS", "PENAL", "CRIMINALISTA*", "TRABALHIST*", "TRABALHO",
            "PREVIDENCI*", "INSS", "APOSENTADORIA*", "BPC", "LOAS", "AUXILIO DOENCA", "CONSUMIDOR",
            "ACIDENTE*", "DANOS MORAIS", "ERRO MEDICO", "TRANSITO", "DPVAT", "MULTAS",
        ],
        "cnaes_principais": {"6911701"},
        # e-mail só vale se for do próprio escritório (domínio/usuário jurídico);
        # e-mail com cara de contador é ignorado (a Receita traz muito e-mail do
        # contador que abriu o CNPJ — "fiscal@xyzcontabil" não diz nada do advogado)
        "email_exige": ["adv", "advoc", "jur", "law", "direito", "legal"],
        # consultoria tributária, consultoria em gestão, holdings
        "cnaes_sinal": {"6920602", "7020400", "6462000", "6463800"},
    },
    "contabil_digital": {
        "tags": {
            "digital": [
                "DIGITAL", "DIGITAIS", "ONLINE", "INTERNET", "VIRTUAL", "WEB", "TECH", "TECNOLOGI*",
                "SAAS", "STARTUP*", "ECOMMERCE", "E COMMERCE", "MARKETPLACE*", "APP",
            ],
            "infoprodutores": [
                "INFOPRODUT*", "INFOPRODUCER*", "CRIADORES", "CREATOR*", "INFLUENC*", "PRODUTORES DIGITAIS",
                "AFILIADO*", "YOUTUBER*", "MARKETING DIGITAL", "HOTMART", "KIWIFY", "EDUZZ",
            ],
            "exportacao_cambio": [
                "EXPORT*", "INTERNACIONA*", "INTERNATIONAL", "EXTERIOR", "ABROAD", "COMEX",
                "CAMBIO", "MULTIMOEDA", "DOLAR*", "DOLLAR*", "NOMADE*", "NOMAD*", "REMOTO", "REMOTE",
                "OFFSHORE", "USA", "EUA", "CROSS BORDER",
            ],
            "traders_cripto": [
                "TRADER*", "TRADING", "DAY TRADE", "DAYTRADE*", "FOREX", "CRIPTO*", "CRYPTO*", "BITCOIN",
                "BLOCKCHAIN", "INVESTIDOR*", "BOLSA DE VALORES", "RENDA VARIAVEL", "DERIVATIVO*",
            ],
        },
        "negativas": [
            "PUBLICA", "PUBLICO", "PUBLICAS", "MUNICIPAL", "MUNICIPIOS", "PREFEITURA*", "CAMARA*",
            "ESCOLA", "EDUCACIONAL", "FACULDADE", "PARA MEDICOS", "PARA DENTISTAS", "AGRO*", "RURAL",
            "CONDOMINIO*", "IGREJA*",
        ],
        "cnaes_principais": {"6920601", "6920602"},
        "email_exige": [],
        # desenvolvimento/licenciamento de software, TI, portais, marketing,
        # intermediação de negócios
        "cnaes_sinal": {
            "6201501", "6202300", "6203100", "6204000", "6209100", "6311900", "6319400",
            "7319002", "7319003", "7319004", "7490104",
        },
    },
}

# E-mail com cara de escritório contábil (vale para todos os nichos de advocacia)
EMAIL_CONTADOR = ["contab", "contador", "contadores", "fiscal", "cnt", "escritorio"]

# Termos do e-mail precisam de 6+ letras (sem espaço) para casar como substring
MIN_EMAIL = 6


def email_texto(email: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normaliza(email))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nicho", required=True, choices=sorted(NICHOS))
    parser.add_argument("entrada", help="CSV de leads do segmento correspondente")
    parser.add_argument("saida", help="CSV só com os leads do nicho")
    parser.add_argument("--aceitar-cnae", action="store_true", help="Aceita lead só por CNAE-sinal, sem keyword")
    args = parser.parse_args()

    nicho = NICHOS[args.nicho]
    tags = {tag: compila_termos(termos) for tag, termos in nicho["tags"].items()}
    negativas = compila_termos(nicho["negativas"])
    tags_email = {
        tag: [(t, normaliza(t.rstrip("*")).replace(" ", "")) for t in termos if len(t.rstrip("*").replace(" ", "")) >= MIN_EMAIL]
        for tag, termos in nicho["tags"].items()
    }
    neg_email = [normaliza(t.rstrip("*")).replace(" ", "") for t in nicho["negativas"] if len(t.rstrip("*").replace(" ", "")) >= MIN_EMAIL]

    with open(args.entrada, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        colunas = list(reader.fieldnames or [])
        leads = list(reader)

    saida, removidos_neg = [], 0
    for lead in leads:
        nome = f"{lead.get('razao_social', '')} {lead.get('nome_fantasia', '')}"
        if (lead.get("cnae_fiscal_principal") or "").strip() not in nicho["cnaes_principais"]:
            continue
        email = email_texto(lead.get("correio_eletronico", ""))
        if nicho["email_exige"] and (
            any(c in email for c in EMAIL_CONTADOR) or not any(e in email for e in nicho["email_exige"])
        ):
            email = ""
        if termos_encontrados(nome, negativas) or any(n in email for n in neg_email):
            removidos_neg += 1
            continue
        achou_tags, termos, fontes = [], [], set()
        for tag, compilados in tags.items():
            no_nome = termos_encontrados(nome, compilados)
            no_email = [t for t, base in tags_email[tag] if base in email]
            if no_nome or no_email:
                achou_tags.append(tag)
                termos += no_nome + [t for t in no_email if t not in no_nome]
                fontes |= ({"nome"} if no_nome else set()) | ({"email"} if no_email else set())
        cnaes = {c.strip() for c in (lead.get("cnae_fiscal_secundaria") or "").split(",") if c.strip()}
        sinais = sorted(cnaes & nicho["cnaes_sinal"])
        if sinais:
            fontes.add("cnae")
        if not achou_tags and not (args.aceitar_cnae and sinais):
            continue
        lead["nicho"] = args.nicho
        lead["nicho_tags"] = "|".join(achou_tags) if achou_tags else "cnae"
        lead["nicho_termos"] = "|".join(dict.fromkeys(termos))
        lead["nicho_fonte"] = "|".join(sorted(fontes))
        lead["nicho_cnaes"] = "|".join(sinais)
        lead["nicho_score"] = str(len(achou_tags) + (1 if "nome" in fontes else 0) + len(sinais))
        saida.append(lead)

    saida.sort(key=lambda l: -int(l["nicho_score"]))  # estável: mantém a relevância anterior no empate
    novas = ["nicho", "nicho_tags", "nicho_termos", "nicho_fonte", "nicho_cnaes", "nicho_score"]
    with open(args.saida, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=colunas + [c for c in novas if c not in colunas])
        writer.writeheader()
        writer.writerows(saida)

    contagem: dict[str, int] = {}
    for l in saida:
        for t in l["nicho_tags"].split("|"):
            contagem[t] = contagem.get(t, 0) + 1
    print(f"{args.nicho}: {len(saida)}/{len(leads)} leads (negativas removeram {removidos_neg}) -> {args.saida}", file=sys.stderr)
    for t, q in sorted(contagem.items(), key=lambda kv: -kv[1]):
        print(f"  {t:<20} {q}", file=sys.stderr)


if __name__ == "__main__":
    main()
