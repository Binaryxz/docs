"""
Estima se o telefone/e-mail cadastrado na Receita é do DONO (e não da
recepção ou do contador) — sem consultar nenhum serviço de terceiros, só com
os dados abertos da Receita. Não confirma titularidade: separa os casos mais
prováveis.

Sinais (colunas novas):
  qtd_socios        nº de sócios do CNPJ (arquivos Socios)
  uma_pessoa        1 se empresário individual (natureza 2135) ou sócio único
                    — o celular cadastrado é quase sempre o do dono
  socio_do_email    nome do sócio cujo nome (ou sobrenome pouco comum) aparece
                    na parte antes do @ do e-mail (ex.: cristianecade@... ->
                    CRISTIANE CADE); vazio se nenhum
  sinal_dono        2 = os dois sinais, 1 = um deles, 0 = nenhum

Quando socio_do_email existe, nome_decisor passa a ser esse sócio (o contato
cadastrado é dele, então a mensagem deve chamá-lo pelo nome) — o decisor
original fica em nome_decisor_original. O arquivo sai reordenado por
sinal_dono (decrescente), mantendo a ordem anterior dentro de cada grupo.

Uso:
    python sinais_dono.py leads_advocacia_wa.csv leads_advocacia_dono.csv \
      --socios "dados_rf/2026-09-14/Socios*.zip" [--minimo 1]
"""

import argparse
import csv
import re
import sys
import unicodedata

from filtrar_cnpjs_segmento import CAMPOS_SOCIOS, abrir_linhas, expandir

csv.field_size_limit(sys.maxsize)

# Sobrenomes comuns demais pra identificar alguém pelo e-mail
SOBRENOMES_COMUNS = {
    "silva", "santos", "souza", "sousa", "oliveira", "pereira", "costa", "lima", "ferreira",
    "rodrigues", "almeida", "alves", "gomes", "ribeiro", "carvalho", "martins", "araujo",
    "barbosa", "junior", "filho", "neto", "sobrinho",
}


def norm(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii").lower()


def tokens_nome(nome: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]{4,}", norm(nome)) if t not in SOBRENOMES_COMUNS}


def socio_do_email(email: str, nomes: list[str]) -> str:
    local = norm(email).split("@")[0]
    if not local:
        return ""
    melhor, melhor_qtd = "", 0
    for nome in nomes:
        qtd = sum(1 for t in tokens_nome(nome) if t in local)
        if qtd > melhor_qtd:
            melhor, melhor_qtd = nome, qtd
    return melhor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", help="CSV de leads (qualquer etapa, precisa de cnpj, natureza_juridica, correio_eletronico)")
    parser.add_argument("saida", help="CSV de saída com os sinais")
    parser.add_argument("--socios", nargs="+", required=True, help="Caminho(s)/glob dos arquivos de Sócios")
    parser.add_argument("--minimo", type=int, default=0, help="Mantém só leads com sinal_dono >= N (padrão 0 = todos)")
    args = parser.parse_args()

    with open(args.entrada, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        colunas = list(reader.fieldnames or [])
        leads = list(reader)

    ids = {(l.get("cnpj") or "")[:8] for l in leads}
    socios: dict[str, list[str]] = {}
    for caminho in expandir(args.socios):
        for row in abrir_linhas(caminho, CAMPOS_SOCIOS):
            b = (row.get("cnpj_basico") or "").strip()
            if b in ids:
                socios.setdefault(b, []).append((row.get("nome_socio_razao_social") or "").strip())

    for lead in leads:
        b = (lead.get("cnpj") or "")[:8]
        nomes = socios.get(b, [])
        individual = (lead.get("natureza_juridica") or "").strip() == "2135"
        lead["qtd_socios"] = str(len(nomes))
        lead["uma_pessoa"] = "1" if individual or len(nomes) == 1 else "0"
        # Empresário individual: o "sócio" é o titular, cujo nome é a razão social
        candidatos = nomes or ([lead.get("razao_social", "")] if individual else [])
        dono_email = socio_do_email(lead.get("correio_eletronico", ""), candidatos)
        lead["socio_do_email"] = dono_email
        lead["sinal_dono"] = str(int(lead["uma_pessoa"]) + (1 if dono_email else 0))
        lead["nome_decisor_original"] = lead.get("nome_decisor", "")
        if dono_email and nomes and dono_email != lead.get("nome_decisor"):
            lead["nome_decisor"] = dono_email
            lead["documento_decisor"] = ""  # documento era do decisor original

    total = len(leads)
    leads = [l for l in leads if int(l["sinal_dono"]) >= args.minimo]
    leads.sort(key=lambda l: -int(l["sinal_dono"]))  # sort estável: mantém a ordem de relevância

    novas = ["qtd_socios", "uma_pessoa", "socio_do_email", "sinal_dono", "nome_decisor_original"]
    with open(args.saida, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=colunas + [c for c in novas if c not in colunas])
        writer.writeheader()
        writer.writerows(leads)

    contagem = {n: sum(1 for l in leads if l["sinal_dono"] == str(n)) for n in (2, 1, 0)}
    print(f"{len(leads)}/{total} leads (sinal_dono 2={contagem[2]} 1={contagem[1]} 0={contagem[0]}) -> {args.saida}", file=sys.stderr)


if __name__ == "__main__":
    main()
