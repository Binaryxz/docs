"""
Etapa 4 — valida o telefone dos leads e aplica as armadilhas já conhecidas
antes de gastar com a checagem de WhatsApp:

  - Formato: reaproveita valida_numero() de
    prospeccao-fintech/scripts/validar_telefone.py SEM mudança (phonenumbers,
    offline). Tenta telefone_1 e, se não for válido, telefone_2.
  - Descarta uf == "EX" (ex.: fundos administrados por DTVM com +551140090000).
  - Descarta placeholder: últimos 9 dígitos com <= 2 dígitos distintos
    (+5511999999999, +5599999999999, ...) -> telefone_status=suspeito_repetido.
  - Mesmo telefone em vários CNPJs: contador/escritório que cadastra o próprio
    número pros clientes. Se um número aparece em mais de
    --max-cnpjs-por-telefone leads, TODOS são descartados
    (telefone_status=suspeito_repetido); se aparece em poucos, fica só o de
    maior relevância (dedupe por TELEFONE, não só por CNPJ).
  - --historico: CSV(s) de contatos já tocados (export do WATI, saídas de
    rodadas anteriores, ...). Qualquer telefone que apareça em qualquer coluna
    desses arquivos é descartado (telefone_status=ja_contatado).

Saídas:
  <saida.csv>  todos os leads com telefone_e164, telefone_tipo,
               telefone_status (ok / sem_numero / nao / uf_ex /
               suspeito_repetido / duplicado / ja_contatado) e
               telefone_qtd_cnpjs.
  --txt-checknumber <arquivo.txt>  só os telefone_status=ok, um E.164 por
               linha — entrada pronta pro checar_whatsapp_checknumber.py.

Uso:
    python preparar_telefones.py leads_advocacia.csv leads_advocacia_tel.csv \
      --txt-checknumber numeros_advocacia.txt \
      --historico wati_contatos_export.csv disparos_anteriores/*.csv
"""

import argparse
import csv
import glob
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "prospeccao-fintech", "scripts"))
from validar_telefone import valida_numero  # noqa: E402

csv.field_size_limit(sys.maxsize)


def digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def chave_telefone(valor: str) -> str:
    """Chave de comparação: DDD + número (sem DDI), pra casar '+5511...',
    '5511...' e '11...' como o mesmo telefone."""
    d = digitos(valor)
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    return d if len(d) in (10, 11) else ""


def eh_placeholder(e164: str) -> bool:
    ultimos_9 = digitos(e164)[-9:]
    return len(set(ultimos_9)) <= 2


def carregar_historico(padroes: list[str]) -> set[str]:
    telefones: set[str] = set()
    for padrao in padroes:
        caminhos = sorted(glob.glob(padrao))
        if not caminhos:
            print(f"[aviso] --historico: nada encontrado para {padrao}", file=sys.stderr)
        for caminho in caminhos:
            with open(caminho, newline="", encoding="utf-8-sig", errors="replace") as f:
                for row in csv.reader(f):
                    for celula in row:
                        k = chave_telefone(celula)
                        if k:
                            telefones.add(k)
            # WATI exporta CountryCode e Phone em colunas separadas — Phone sem o
            # 55 já vira chave acima (10/11 dígitos); nada extra a fazer.
    return telefones


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", help="CSV de leads (saída do filtrar_cnpjs_segmento.py)")
    parser.add_argument("saida", help="CSV de saída com colunas de telefone")
    parser.add_argument("--txt-checknumber", default=None, help="Gera .txt (um E.164 por linha) só com telefone_status=ok")
    parser.add_argument("--historico", nargs="+", default=[], help="CSV(s)/glob de contatos já tocados — excluídos")
    parser.add_argument("--max-cnpjs-por-telefone", type=int, default=3, help="Acima disso, o número é tratado como do contador/placeholder (padrão 3)")
    parser.add_argument("--so-celular", action="store_true", help="Descarta fixo (telefone_status=fixo) — WhatsApp em fixo é raro")
    args = parser.parse_args()

    with open(args.entrada, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        colunas_entrada = list(reader.fieldnames or [])
        leads = list(reader)

    for lead in leads:
        lead["telefone_e164"] = ""
        lead["telefone_tipo"] = ""
        lead["telefone_9_adicionado"] = ""
        status = "sem_numero"
        for ddd_col, num_col in (("ddd_1", "telefone_1"), ("ddd_2", "telefone_2")):
            bruto = (lead.get(ddd_col) or "").strip() + (lead.get(num_col) or "").strip()
            if not bruto:
                continue
            v = valida_numero(bruto)
            if v["telefone_formato_valido"] in ("sim", "suspeito_repetido"):
                lead["telefone_e164"] = v["telefone_e164"]
                lead["telefone_tipo"] = v["telefone_tipo"]
                lead["telefone_9_adicionado"] = v["telefone_9_adicionado"]
                status = "ok" if v["telefone_formato_valido"] == "sim" and not eh_placeholder(v["telefone_e164"]) else "suspeito_repetido"
                if status == "ok":
                    break
            elif status == "sem_numero":
                status = "nao"
        if (lead.get("uf") or "").strip().upper() == "EX":
            status = "uf_ex"
        if status == "ok" and args.so_celular and lead["telefone_tipo"] == "fixo":
            status = "fixo"
        lead["telefone_status"] = status

    # Frequência de cada telefone na lista (entre os que ainda estão ok)
    por_telefone: dict[str, list[dict]] = {}
    for lead in leads:
        if lead["telefone_status"] == "ok":
            por_telefone.setdefault(chave_telefone(lead["telefone_e164"]), []).append(lead)
    for grupo in por_telefone.values():
        for lead in grupo:
            lead["telefone_qtd_cnpjs"] = str(len(grupo))
        if len(grupo) > args.max_cnpjs_por_telefone:
            for lead in grupo:
                lead["telefone_status"] = "suspeito_repetido"
        elif len(grupo) > 1:
            def _rank(l: dict) -> tuple:
                try:
                    rel = float(l.get("relevancia") or 0)
                except ValueError:
                    rel = 0.0
                return (-rel, 0 if l.get("eh_decisor") == "1" else 1, l.get("cnpj", ""))
            for lead in sorted(grupo, key=_rank)[1:]:
                lead["telefone_status"] = "duplicado"

    historico = carregar_historico(args.historico) if args.historico else set()
    if historico:
        print(f"histórico: {len(historico)} telefones já contatados carregados", file=sys.stderr)
        for lead in leads:
            if lead["telefone_status"] == "ok" and chave_telefone(lead["telefone_e164"]) in historico:
                lead["telefone_status"] = "ja_contatado"

    novas = ["telefone_e164", "telefone_tipo", "telefone_9_adicionado", "telefone_status", "telefone_qtd_cnpjs"]
    with open(args.saida, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=colunas_entrada + [c for c in novas if c not in colunas_entrada], extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            lead.setdefault("telefone_qtd_cnpjs", "")
            writer.writerow(lead)

    contagem: dict[str, int] = {}
    for lead in leads:
        contagem[lead["telefone_status"]] = contagem.get(lead["telefone_status"], 0) + 1
    print(f"total: {len(leads)}", file=sys.stderr)
    for status, qtd in sorted(contagem.items(), key=lambda kv: -kv[1]):
        print(f"  {status:<18} {qtd}", file=sys.stderr)

    if args.txt_checknumber:
        numeros = sorted({l["telefone_e164"] for l in leads if l["telefone_status"] == "ok"})
        with open(args.txt_checknumber, "w", encoding="utf-8") as f:
            f.write("\n".join(numeros) + ("\n" if numeros else ""))
        print(f"{len(numeros)} números gravados em {args.txt_checknumber}", file=sys.stderr)
    print(f"gravado em {args.saida}", file=sys.stderr)


if __name__ == "__main__":
    main()
