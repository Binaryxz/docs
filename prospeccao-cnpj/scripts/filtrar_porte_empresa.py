"""
Filtra leads pelo porte (nº de funcionários), SEM o resto do enriquecimento
(site/telefone/WhatsApp/LinkedIn do decisor) — só a parte de descartar
empresa grande demais, pra rodar como etapa isolada antes do enriquecimento
completo e economizar chamadas de API em quem já vai ser descartado.

Reaproveita a lógica de achar_porte_empresa/brave_search/nome_base de
enrich_leads_gratis.py (mesma fonte, sem duplicar) — é a mesma heurística
usada lá com --max-funcionarios, só que como etapa independente.

Como funciona: busca a página da empresa no LinkedIn via Brave Search e
extrai a faixa "X-Y employees" que aparece no resumo. Classifica em:
  - sim:      toda a faixa está dentro do limite (ex.: limite 50, achou "11-50")
  - nao:      toda a faixa está acima do limite (ex.: limite 50, achou "201-500")
  - provavel: o limite cai DENTRO da faixa achada (ex.: limite 40, achou "11-50")
              — nesse caso o lead é mantido (falso negativo é pior que falso
              positivo aqui); a coluna porte_linkedin mostra a faixa real pra
              conferência manual.
  - "" (vazio): não achou a página da empresa no LinkedIn / sem faixa visível.

Só remove do CSV de saída quem ficou classificado como "nao". Todo o resto
(sim, provavel, vazio) permanece — não sabemos o porte real desses, então
não descartamos.

Requisitos: só BRAVE_API_KEY (https://brave.com/search/api/) — confira saldo
antes de rodar em lote (essa conta costuma ser pré-paga por unidades, não
plano gratuito simples; ver aviso em enrich_leads_gratis.py).

Uso:
    export BRAVE_API_KEY="sua-chave"
    python filtrar_porte_empresa.py leads.csv leads_filtrados.csv --max-funcionarios 40
"""

import csv
import os
import sys
import time

from enrich_leads_gratis import achar_porte_empresa, brave_search

SECONDS_BETWEEN_CALLS = 1.0


def classifica_porte(lead: dict, brave_api_key: str, max_funcionarios: int) -> dict:
    razao_social = lead.get("razao_social", "")
    nome_fantasia = lead.get("nome_fantasia", "")
    try:
        resultados = brave_search(f"{razao_social} linkedin funcionarios employees", brave_api_key)
    except Exception as exc:
        print(f"[aviso] busca de porte falhou p/ {razao_social}: {exc}", file=sys.stderr)
        resultados = []

    porte_linkedin, faixa = achar_porte_empresa(resultados, razao_social, nome_fantasia)
    porte_ate_limite = ""
    if faixa:
        baixo, alto = faixa
        if alto <= max_funcionarios:
            porte_ate_limite = "sim"
        elif baixo > max_funcionarios:
            porte_ate_limite = "nao"
        else:
            porte_ate_limite = "provavel"

    return {"porte_linkedin": porte_linkedin, "porte_ate_limite": porte_ate_limite}


def main(input_path: str, output_path: str, max_funcionarios: int) -> None:
    brave_api_key = os.environ["BRAVE_API_KEY"]

    with open(input_path, newline="", encoding="utf-8") as f_in:
        leads = list(csv.DictReader(f_in))
    if not leads:
        print("Nenhum lead no CSV de entrada.", file=sys.stderr)
        return

    campos_saida = list(leads[0].keys()) + ["porte_linkedin", "porte_ate_limite"]
    excluidos = 0

    with open(output_path, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=campos_saida)
        writer.writeheader()

        for i, lead in enumerate(leads, start=1):
            print(f"[{i}/{len(leads)}] checando porte de {lead.get('razao_social')}...", file=sys.stderr)
            resultado = classifica_porte(lead, brave_api_key, max_funcionarios)
            if resultado["porte_ate_limite"] == "nao":
                excluidos += 1
            else:
                writer.writerow({**lead, **resultado})
            time.sleep(SECONDS_BETWEEN_CALLS)

    print(f"[info] {excluidos} leads excluídos por terem mais de {max_funcionarios} funcionários (LinkedIn)", file=sys.stderr)
    print("CONCLUIDO", file=sys.stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", help="CSV de entrada")
    parser.add_argument("saida", help="CSV de saída (só quem passou no filtro de porte)")
    parser.add_argument("--max-funcionarios", type=int, required=True, help="Limite de funcionários (ex.: 40)")
    args = parser.parse_args()
    main(args.entrada, args.saida, args.max_funcionarios)
