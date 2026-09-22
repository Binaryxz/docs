"""
Valida telefones dos leads: formato (DDD real, quantidade de dígitos, se é
celular ou fixo) via a biblioteca `phonenumbers` (port do libphonenumber do
Google) — grátis, offline, sem limite de volume. Detecta número claramente
quebrado (DDD inexistente, dígitos faltando/sobrando) antes do disparo.

Isso NÃO confirma que a linha está ativa/em uso — só que o número é
estruturalmente válido no plano de numeração brasileiro. Validação de
"está ativa de verdade" (ex.: Numverify) tem custo por requisição e não
está integrada aqui — o free tier (100/mês) não cobre volumes de milhares.

Uso:
    python validar_telefone.py entrada.csv saida.csv \
      --coluna-telefone telefone [--coluna-ddd ddd_1 --coluna-numero telefone_1]
"""

import argparse
import sys

import pandas as pd
import phonenumbers


TIPO_NOME = {
    phonenumbers.PhoneNumberType.MOBILE: "celular",
    phonenumbers.PhoneNumberType.FIXED_LINE: "fixo",
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixo_ou_celular",
    phonenumbers.PhoneNumberType.VOIP: "voip",
    phonenumbers.PhoneNumberType.UNKNOWN: "desconhecido",
}


def _tenta_parse(candidato: str) -> phonenumbers.PhoneNumber | None:
    try:
        parsed = phonenumbers.parse(candidato, "BR")
    except phonenumbers.NumberParseException:
        return None
    return parsed if phonenumbers.is_valid_number(parsed) else None


def valida_numero(numero_bruto: str) -> dict:
    if not numero_bruto or not isinstance(numero_bruto, str) or not numero_bruto.strip():
        return {"telefone_formato_valido": "sem_numero", "telefone_tipo": "", "telefone_e164": "", "telefone_9_adicionado": "nao"}

    digitos = "".join(c for c in numero_bruto.strip() if c.isdigit())
    if digitos.startswith("55") and len(digitos) > 11:
        digitos = digitos[2:]  # remove DDI, o parse abaixo já assume BR

    candidato = "+55" + digitos
    parsed = _tenta_parse(candidato)
    nove_adicionado = False

    # Celular pré-2012 tem 8 dígitos locais (sem o "9" que passou a ser
    # obrigatório) — a Receita muitas vezes nunca atualizou o cadastro.
    # DDD (2 dígitos) + número local de 8 dígitos começando em 6-9 é quase
    # sempre esse caso: tenta de novo com o "9" inserido antes de desistir.
    if parsed is None and len(digitos) == 10 and digitos[2] in "6789":
        candidato_com_9 = "+55" + digitos[:2] + "9" + digitos[2:]
        parsed_com_9 = _tenta_parse(candidato_com_9)
        if parsed_com_9 is not None:
            parsed = parsed_com_9
            nove_adicionado = True

    if parsed is None:
        return {"telefone_formato_valido": "nao", "telefone_tipo": "", "telefone_e164": "", "telefone_9_adicionado": "nao"}

    # Estruturalmente válido não significa real: "62999999999" passa no
    # libphonenumber mas é claramente placeholder. Sinaliza dígito local
    # repetido (todos iguais) como suspeito em vez de "válido" sem ressalva.
    local = ("9" + digitos[2:]) if nove_adicionado else digitos[2:]
    formato = "suspeito_repetido" if len(set(local)) == 1 else "sim"

    tipo_nome = TIPO_NOME.get(phonenumbers.number_type(parsed), "outro")
    return {
        "telefone_formato_valido": formato,
        "telefone_tipo": tipo_nome,
        "telefone_e164": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        "telefone_9_adicionado": "sim" if nove_adicionado else "nao",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", help="CSV de entrada")
    parser.add_argument("saida", help="CSV de saída (com colunas de validação adicionadas)")
    parser.add_argument("--coluna-telefone", default=None, help="Coluna com telefone já completo (DDD+número)")
    parser.add_argument("--coluna-ddd", default="ddd_1", help="Coluna de DDD, se telefone vier separado (padrão ddd_1)")
    parser.add_argument("--coluna-numero", default="telefone_1", help="Coluna de número, se telefone vier separado (padrão telefone_1)")
    args = parser.parse_args()

    df = pd.read_csv(args.entrada, dtype=str)

    if args.coluna_telefone and args.coluna_telefone in df.columns:
        brutos = df[args.coluna_telefone].fillna("")
    else:
        ddd = df.get(args.coluna_ddd, pd.Series([""] * len(df))).fillna("")
        numero = df.get(args.coluna_numero, pd.Series([""] * len(df))).fillna("")
        brutos = ddd + numero

    resultados = [valida_numero(b) for b in brutos]
    resultado_df = pd.DataFrame(resultados)
    saida = pd.concat([df.reset_index(drop=True), resultado_df], axis=1)
    saida.to_csv(args.saida, index=False)

    print(f"total: {len(saida)}", file=sys.stderr)
    print(saida["telefone_formato_valido"].value_counts().to_string(), file=sys.stderr)
    print(f"gravado em {args.saida}", file=sys.stderr)


if __name__ == "__main__":
    main()
