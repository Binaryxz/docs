"""
Etapa 6 — gera os dois formatos de saída a partir dos leads já com telefone
validado (e, idealmente, tem_whatsapp do checar_whatsapp_checknumber.py):

  1. Template de disparo: nome,primeiro_nome,telefone,email,regiao,valor
  2. WATI (importação de campanha):
     Name,CountryCode,Phone,AllowCampaign,AllowSMS,Attribute 1,Attribute 2
       CountryCode = 55, Phone = telefone sem o 55,
       Attribute 1 = primeiro nome do decisor, Attribute 2 = UF

Entram só leads com telefone_status=ok e — se a coluna tem_whatsapp existir —
tem_whatsapp=sim (use --incluir-sem-checagem para aceitar quem ainda não foi
checado). A ordem segue a do arquivo de entrada (já ordenado por relevância).

--limite N corta a lista nos N primeiros: use o teto diário de contatos únicos
do Meta/WATI (card "Your daily Meta messaging limit" no painel do WATI).
Disparar acima dele gera "Failed" em massa — e reenviar o mesmo lote no mesmo
dia só empilha falha. O que sobrar vai pra --resto, pra rodar no dia seguinte.

Mapeamento:
  nome          <- razao_social
  primeiro_nome <- primeiro nome do decisor (capitalizado); sem decisor e
                   empresário individual -> 1a palavra da razão social se
                   for nome (>= 3 letras); senão vazio
  telefone      <- telefone_e164 sem "+" (55 + DDD + número)
  email         <- correio_eletronico (Receita)
  regiao        <- nome_municipio/UF (ou só UF)
  valor         <- vazio
  Name (WATI)   <- razao_social

Uso:
    python gerar_saidas.py leads_advocacia_wa.csv \
      --disparo disparo_advocacia.csv --wati wati_advocacia.csv \
      [--limite 1000 --resto resto_advocacia.csv]
"""

import argparse
import csv
import re
import sys

csv.field_size_limit(sys.maxsize)

COLUNAS_DISPARO = ["nome", "primeiro_nome", "telefone", "email", "regiao", "valor"]
COLUNAS_WATI = ["Name", "CountryCode", "Phone", "AllowCampaign", "AllowSMS", "Attribute 1", "Attribute 2"]

# Partículas que não são primeiro nome quando aparecem antes do nome
# (raro na Receita, mas acontece em nome de sócio PJ/espólio).
NAO_NOME = {"espolio", "de", "da", "do", "dos", "das"}


def capitaliza(palavra: str) -> str:
    return palavra[:1].upper() + palavra[1:].lower()


def primeiro_nome(lead: dict) -> str:
    # Sócio PJ (CNPJ de 14 dígitos) não tem "primeiro nome" de pessoa
    doc = re.sub(r"\D", "", lead.get("documento_decisor", ""))
    nome_decisor = (lead.get("nome_decisor") or "").strip()
    if nome_decisor and len(doc) != 14:
        for palavra in nome_decisor.split():
            if palavra.lower() not in NAO_NOME:
                return capitaliza(palavra)
    # Empresário individual (natureza 2135) não tem sócio: a razão social é o
    # nome do dono. Só usa se a 1a palavra for nome mesmo — "J", "W", "R."
    # viram vazio em vez de um primeiro nome errado. O nome fantasia nunca é
    # usado ("Consult", "Alpha"... saíam como primeiro nome).
    if (lead.get("natureza_juridica") or "").strip() == "2135":
        palavras = (lead.get("razao_social") or "").split()
        if palavras and len(palavras[0]) >= 3 and palavras[0].isalpha():
            return capitaliza(palavras[0])
    return ""


RX_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)+")


def limpa_email(bruto: str) -> str:
    """A Receita traz e-mail com lixo (aspas, espaço, ponto no fim...). Fica o
    primeiro trecho com formato de e-mail; sem isso, vazio."""
    m = RX_EMAIL.search((bruto or "").strip().lower())
    return m.group(0).rstrip(".-") if m else ""


def regiao(lead: dict) -> str:
    municipio = (lead.get("nome_municipio") or "").strip()
    uf = (lead.get("uf") or "").strip()
    if municipio and uf:
        return f"{municipio.title()}/{uf}"
    return municipio.title() or uf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", help="CSV de leads (preparar_telefones.py, idealmente mesclado com tem_whatsapp)")
    parser.add_argument("--disparo", required=True, help="CSV de saída no template de disparo")
    parser.add_argument("--wati", required=True, help="CSV de saída para importação no WATI")
    parser.add_argument("--limite", type=int, default=None, help="Máximo de contatos (teto diário do Meta/WATI)")
    parser.add_argument("--resto", default=None, help="CSV com os leads aptos que passaram do --limite (para o dia seguinte)")
    parser.add_argument("--incluir-sem-checagem", action="store_true", help="Aceita leads sem tem_whatsapp preenchido")
    args = parser.parse_args()

    with open(args.entrada, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        colunas = list(reader.fieldnames or [])
        leads = list(reader)

    tem_coluna_wa = "tem_whatsapp" in colunas
    if not tem_coluna_wa:
        print("[aviso] entrada sem coluna tem_whatsapp — WhatsApp NÃO foi checado", file=sys.stderr)

    aptos = []
    vistos: set[str] = set()
    for lead in leads:
        if lead.get("telefone_status", "ok") != "ok":
            continue
        wa = (lead.get("tem_whatsapp") or "").strip()
        if tem_coluna_wa and wa != "sim" and not (args.incluir_sem_checagem and wa == ""):
            continue
        telefone = re.sub(r"\D", "", lead.get("telefone_e164", ""))
        if not telefone.startswith("55") or telefone in vistos:
            continue
        vistos.add(telefone)
        aptos.append((lead, telefone))

    hoje, resto = (aptos[: args.limite], aptos[args.limite :]) if args.limite is not None else (aptos, [])

    with open(args.disparo, "w", newline="", encoding="utf-8") as f_d, open(args.wati, "w", newline="", encoding="utf-8") as f_w:
        w_d = csv.DictWriter(f_d, fieldnames=COLUNAS_DISPARO)
        w_w = csv.DictWriter(f_w, fieldnames=COLUNAS_WATI)
        w_d.writeheader()
        w_w.writeheader()
        for lead, telefone in hoje:
            nome = (lead.get("razao_social") or "").strip()
            pn = primeiro_nome(lead)
            w_d.writerow(
                {
                    "nome": nome,
                    "primeiro_nome": pn,
                    "telefone": telefone,
                    "email": limpa_email(lead.get("correio_eletronico", "")),
                    "regiao": regiao(lead),
                    "valor": "",
                }
            )
            w_w.writerow(
                {
                    "Name": nome,
                    "CountryCode": "55",
                    "Phone": telefone[2:],
                    "AllowCampaign": "TRUE",
                    "AllowSMS": "FALSE",
                    "Attribute 1": pn,
                    "Attribute 2": (lead.get("uf") or "").strip(),
                }
            )

    if resto:
        if args.resto:
            with open(args.resto, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=colunas)
                writer.writeheader()
                writer.writerows(l for l, _ in resto)
            print(f"{len(resto)} leads aptos acima do --limite gravados em {args.resto}", file=sys.stderr)
        else:
            print(f"[aviso] {len(resto)} leads aptos ficaram de fora pelo --limite (use --resto para guardá-los)", file=sys.stderr)

    print(f"{len(hoje)} contatos -> {args.disparo} e {args.wati} (de {len(leads)} leads na entrada)", file=sys.stderr)


if __name__ == "__main__":
    main()
