"""
Adapta a saída do pipeline (leads + enriquecimento IA) para o formato de
importação de uma ferramenta de terceiros (ex.: disparo de WhatsApp/CRM),
cujo modelo tem colunas: nome, primeiro_nome, telefone, email, regiao, valor.

Uso:
    python adaptar_formato_importacao.py \
      leads_fintech_480.csv leads_enriquecidos.csv saida_importacao.csv \
      [--fonte-original Base_Startups.xlsx]

--fonte-original é opcional: se informado (a planilha original de onde vieram
os leads, com colunas de e-mail/telefone brutos), usa telefone/e-mail dessa
fonte como fallback para quem a IA não encontrou.

Mapeamento:
  nome          <- razao_social
  primeiro_nome <- primeira palavra de nome_fantasia (ou razao_social)
  telefone      <- telefone_comercial_ia > whatsapp_publico > telefone da
                   fonte original, normalizado para dígitos com DDI 55
  email         <- primeiro e-mail encontrado na fonte original (se houver)
  regiao        <- municipio
  valor         <- vazio (sem dado de valor de negócio no pipeline)
"""

import argparse
import csv
import re
import sys

import pandas as pd

COLUNAS_MODELO = ["nome", "primeiro_nome", "telefone", "email", "regiao", "valor"]


def normaliza_telefone(bruto: str) -> str:
    if not bruto or not isinstance(bruto, str):
        return ""
    digitos = re.sub(r"\D", "", bruto)
    if not digitos:
        return ""
    if digitos.startswith("55") and len(digitos) in (12, 13):
        return digitos
    if len(digitos) in (10, 11):
        return "55" + digitos
    return digitos


def primeiro_email(row: dict, colunas_email: list[str]) -> str:
    for col in colunas_email:
        valor = row.get(col)
        if valor and isinstance(valor, str) and valor.strip():
            return valor.split(",")[0].strip()
    return ""


def primeiro_telefone_bruto(row: dict, colunas_telefone: list[str]) -> str:
    for col in colunas_telefone:
        valor = row.get(col)
        if valor and isinstance(valor, str) and valor.strip():
            return valor.split(",")[0].strip()
    return ""


def main(leads_path: str, enriquecidos_path: str, output_path: str, fonte_original: str | None) -> None:
    leads = pd.read_csv(leads_path, dtype=str)
    enriquecidos = pd.read_csv(enriquecidos_path, dtype=str)

    colunas_ia = [c for c in ["telefone_comercial_ia", "whatsapp_publico"] if c in enriquecidos.columns]
    planilha = leads.merge(enriquecidos[["cnpj", *colunas_ia]], on="cnpj", how="left")

    fallback_por_cnpj: dict[str, dict] = {}
    if fonte_original:
        origem = pd.read_excel(fonte_original) if fonte_original.endswith((".xlsx", ".xls")) else pd.read_csv(fonte_original, dtype=str)
        colunas_email = [c for c in origem.columns if c.startswith("lst_emails") or c in ("tu_lst_emails", "ss_lst_emails")]
        colunas_telefone = [c for c in origem.columns if c in ("lkd_lst_telefones", "lst_telefones")]
        for _, row in origem.iterrows():
            cnpj = row.get("cnpj")
            if not isinstance(cnpj, str) or not cnpj.strip():
                continue
            fallback_por_cnpj[cnpj] = {
                "email": primeiro_email(row, colunas_email),
                "telefone": primeiro_telefone_bruto(row, colunas_telefone),
            }

    linhas_saida = []
    for _, row in planilha.iterrows():
        cnpj = row.get("cnpj", "")
        fallback = fallback_por_cnpj.get(cnpj, {})

        telefone = ""
        for candidato in [row.get("telefone_comercial_ia"), row.get("whatsapp_publico"), fallback.get("telefone")]:
            telefone = normaliza_telefone(candidato) if candidato else ""
            if telefone:
                break

        nome_fantasia = row.get("nome_fantasia") or row.get("razao_social") or ""
        primeiro_nome = nome_fantasia.split()[0] if nome_fantasia.split() else ""

        linhas_saida.append(
            {
                "nome": row.get("razao_social", ""),
                "primeiro_nome": primeiro_nome,
                "telefone": telefone,
                "email": fallback.get("email", ""),
                "regiao": row.get("municipio", ""),
                "valor": "",
            }
        )

    with open(output_path, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=COLUNAS_MODELO)
        writer.writeheader()
        writer.writerows(linhas_saida)

    com_telefone = sum(1 for l in linhas_saida if l["telefone"])
    com_email = sum(1 for l in linhas_saida if l["email"])
    print(f"Gerado {output_path} com {len(linhas_saida)} linhas ({com_telefone} com telefone, {com_email} com email).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("leads", help="CSV de leads filtrados (ex.: leads_com_socios.csv ou leads_fintech_480.csv)")
    parser.add_argument("enriquecidos", help="CSV com o enriquecimento da IA")
    parser.add_argument("saida", help="CSV de saída no formato de importação")
    parser.add_argument("--fonte-original", default=None, help="Planilha original (xlsx/csv) com e-mails/telefones brutos, para fallback")
    args = parser.parse_args()
    main(args.leads, args.enriquecidos, args.saida, args.fonte_original)
