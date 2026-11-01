"""
Perfil da CARTEIRA de clientes de cada escritório contábil, só com dados
abertos da Receita.

O contador costuma cadastrar o próprio e-mail e telefone nos CNPJs dos
clientes que abre/mantém. Este script varre todos os Estabelecimentos ativos
e, para cada lead contábil, conta quantos OUTROS CNPJs usam o mesmo e-mail
(domínio próprio, ou o endereço inteiro se for webmail) ou o mesmo telefone —
a carteira provável — e quantos desses clientes têm CNAE principal de negócio
digital, por grupo:

  infoprodutos   cursos/treinamento online, ensino livre, marketing/publicidade,
                 portais e provedores de conteúdo, edição
  tech_servicos  software, TI, design — perfil típico de exportador de serviço
  investimentos  gestão de recursos, agentes de investimento, participações

Colunas novas: carteira_total, carteira_digital, carteira_infoprodutos,
carteira_tech_servicos, carteira_investimentos, carteira_pct_digital. A saída
sai ordenada por carteira_digital (desc).

Uso:
    python carteira_contador.py leads_contabil_tel.csv carteira_contabil.csv \
      --estabelecimentos "dados_rf/2026-09-14/Estabelecimentos*.zip"
"""

import argparse
import csv
import re
import sys

from filtrar_cnpjs_segmento import CAMPOS_ESTABELECIMENTOS, abrir_linhas, expandir

csv.field_size_limit(sys.maxsize)

GRUPOS = {
    "infoprodutos": {
        "8599604", "8599699", "8593700", "8599603",  # treinamento, ensino livre, idiomas, informática
        "7319002", "7319003", "7319004", "7311400",  # promoção, marketing direto, consultoria em publicidade, agências
        "6319400", "5811500", "5819100", "7420001",  # portais/conteúdo, edição, fotografia
    },
    "tech_servicos": {
        "6201501", "6201502", "6202300", "6203100", "6204000", "6209100", "6311900", "7410202", "7410203",
    },
    "investimentos": {"6630400", "6612605", "6619399", "6463800", "6462000"},
}
WEBMAIL = {
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com.br", "yahoo.com", "live.com", "uol.com.br",
    "bol.com.br", "terra.com.br", "ig.com.br", "icloud.com", "hotmail.com.br", "outlook.com.br", "globo.com",
    "msn.com",
}


# Uma chave compartilhada por mais CNPJs que isso não é carteira de um
# escritório: é placeholder, provedor genérico ou plataforma nacional
LIMITE_CHAVE = 3000


def chave_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    dominio = email.split("@", 1)[1].strip()
    if "." not in dominio or len(set(re.sub(r"[^a-z]", "", dominio))) <= 2 or dominio.startswith(("gmail.", "hotmail.", "yahoo.", "outlook.")):
        return "e:" + email if dominio.startswith(("gmail.", "hotmail.", "yahoo.", "outlook.")) else ""
    return "e:" + (email if dominio in WEBMAIL else dominio)


def chave_tel(ddd: str, tel: str) -> str:
    d = re.sub(r"\D", "", (ddd or "") + (tel or ""))
    if len(d) < 10 or len(set(d[-8:])) == 1:  # placeholder (00000000, 11111111...)
        return ""
    return "t:" + d


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", help="CSV de leads contábeis")
    parser.add_argument("saida")
    parser.add_argument("--estabelecimentos", nargs="+", required=True)
    args = parser.parse_args()

    with open(args.entrada, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        colunas = list(reader.fieldnames or [])
        leads = list(reader)

    chaves_do_lead: dict[str, set[str]] = {}
    for lead in leads:
        ks = {chave_email(lead.get("correio_eletronico", "")), chave_tel(lead.get("ddd_1", ""), lead.get("telefone_1", "")),
              chave_tel(lead.get("ddd_2", ""), lead.get("telefone_2", ""))} - {""}
        chaves_do_lead[lead["cnpj"][:8]] = ks
    alvo = set().union(*chaves_do_lead.values()) if chaves_do_lead else set()

    # por chave: CNPJs-base de clientes e grupo de cada um
    clientes: dict[str, dict[str, str]] = {k: {} for k in alvo}
    for caminho in expandir(args.estabelecimentos):
        for row in abrir_linhas(caminho, CAMPOS_ESTABELECIMENTOS):
            if row.get("situacao_cadastral") != "02":
                continue
            ks = {chave_email(row.get("correio_eletronico", "")), chave_tel(row.get("ddd_1", ""), row.get("telefone_1", "")),
                  chave_tel(row.get("ddd_2", ""), row.get("telefone_2", ""))} & alvo
            if not ks:
                continue
            principal = (row.get("cnae_fiscal_principal") or "").strip()
            grupo = next((g for g, cs in GRUPOS.items() if principal in cs), "")
            for k in ks:
                clientes[k][row["cnpj_basico"]] = grupo
        print(f"  lido {caminho}", file=sys.stderr)

    descartadas = {k for k, v in clientes.items() if len(v) > LIMITE_CHAVE}
    print(f"  {len(descartadas)} chave(s) com mais de {LIMITE_CHAVE} CNPJs descartadas (genéricas): "
          + ", ".join(sorted(descartadas)[:10]), file=sys.stderr)
    for lead in leads:
        b = lead["cnpj"][:8]
        carteira: dict[str, str] = {}
        for k in chaves_do_lead[b] - descartadas:
            carteira.update(clientes.get(k, {}))
        carteira.pop(b, None)  # o próprio escritório não é cliente
        total = len(carteira)
        por_grupo = {g: sum(1 for v in carteira.values() if v == g) for g in GRUPOS}
        digital = sum(por_grupo.values())
        lead["carteira_total"] = str(total)
        lead["carteira_digital"] = str(digital)
        for g in GRUPOS:
            lead[f"carteira_{g}"] = str(por_grupo[g])
        lead["carteira_pct_digital"] = f"{digital / total:.2f}" if total else "0.00"

    leads.sort(key=lambda l: (-int(l["carteira_digital"]), -float(l["carteira_pct_digital"])))
    novas = ["carteira_total", "carteira_digital"] + [f"carteira_{g}" for g in GRUPOS] + ["carteira_pct_digital"]
    with open(args.saida, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=colunas + [c for c in novas if c not in colunas])
        writer.writeheader()
        writer.writerows(leads)

    com = [l for l in leads if int(l["carteira_total"]) > 0]
    print(f"{len(com)}/{len(leads)} contadores com carteira identificada; "
          f"{sum(1 for l in leads if int(l['carteira_digital']) >= 5)} com 5+ clientes digitais -> {args.saida}", file=sys.stderr)


if __name__ == "__main__":
    main()
