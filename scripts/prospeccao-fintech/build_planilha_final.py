"""
Junta a base filtrada (Receita + sócios/decisor) com o enriquecimento da IA
e gera a planilha final (.xlsx) com as colunas novas.

Uso:
    python build_planilha_final.py leads_com_socios.csv leads_enriquecidos.csv planilha_final.xlsx
"""

import sys

import pandas as pd

# Todas as colunas que a etapa de IA pode gerar. Só as que realmente
# existirem em <enriquecidos.csv> são usadas — assim tanto uma saída antiga
# (sem linkedin_decisor/telefone_decisor_ia) quanto uma nova funcionam.
NOVAS_COLUNAS_IA = [
    "site_oficial",
    "telefone_comercial_ia",
    "whatsapp_publico",
    "linkedin_decisor",
    "telefone_decisor_ia",
    "fonte_ia",
    "confianca_ia",
]


def main(leads_path: str, enriquecidos_path: str, output_path: str) -> None:
    leads = pd.read_csv(leads_path, dtype=str)
    enriquecidos = pd.read_csv(enriquecidos_path, dtype=str)
    colunas_presentes = [c for c in NOVAS_COLUNAS_IA if c in enriquecidos.columns]
    enriquecidos = enriquecidos[["cnpj", *colunas_presentes]]

    planilha_final = leads.merge(enriquecidos, on="cnpj", how="left")
    planilha_final.to_excel(output_path, index=False, sheet_name="Leads Fintech")
    print(f"Planilha final gerada em {output_path} com {len(planilha_final)} linhas.")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(
            "Uso: python build_planilha_final.py <leads.csv> <enriquecidos.csv> <saida.xlsx>",
            file=sys.stderr,
        )
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
