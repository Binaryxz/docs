#!/usr/bin/env bash
# Baixa a base aberta de CNPJ da Receita Federal (mirror Casa dos Dados):
# Estabelecimentos{0-9}.zip, Empresas{0-9}.zip, Socios{0-9}.zip (30 zips) +
# Municipios.zip (tabela código -> nome do município, usada na saída).
#
# Uso:
#   ./baixar_base_receita.sh [destino] [snapshot]
#     destino   pasta de saída (padrão: ./dados_rf); os zips vão pra <destino>/<snapshot>/
#     snapshot  ex.: 2026-09-14 (padrão: o mais recente listado no mirror)
#
# Variável PARALELO controla downloads simultâneos (padrão 4).
#
# ATENÇÃO: nunca coloque `--` antes da URL junto com `-o arquivo.zip` — o curl
# passa a tratar `-o` e o nome do arquivo como URLs extras e o binário vaza
# pro stdout/log. Aqui é `curl -sS --retry 3 -o {nome} {url}`, sem `--`.
set -euo pipefail

BASE="https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos"
DESTINO="${1:-./dados_rf}"
SNAPSHOT="${2:-}"
PARALELO="${PARALELO:-4}"

if [[ -z "$SNAPSHOT" ]]; then
  SNAPSHOT="$(curl -sS "$BASE/" | grep -oE 'href="[0-9]{4}-[0-9]{2}-[0-9]{2}/"' \
    | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort -u | tail -1)"
  if [[ -z "$SNAPSHOT" ]]; then
    echo "não consegui descobrir o snapshot mais recente em $BASE/" >&2
    exit 1
  fi
fi
echo "snapshot: $SNAPSHOT" >&2

PASTA="$DESTINO/$SNAPSHOT"
mkdir -p "$PASTA"

ARQUIVOS=(Municipios.zip)
for tipo in Estabelecimentos Empresas Socios; do
  for i in 0 1 2 3 4 5 6 7 8 9; do
    ARQUIVOS+=("$tipo$i.zip")
  done
done

# Pula o que já foi baixado e passa no teste de integridade do zip (permite
# retomar um download interrompido sem baixar tudo de novo).
PENDENTES=()
for nome in "${ARQUIVOS[@]}"; do
  if [[ -s "$PASTA/$nome" ]] && unzip -tqq "$PASTA/$nome" >/dev/null 2>&1; then
    continue
  fi
  PENDENTES+=("$nome")
done
echo "${#PENDENTES[@]} de ${#ARQUIVOS[@]} arquivo(s) a baixar em $PASTA" >&2

if (( ${#PENDENTES[@]} )); then
  printf '%s\n' "${PENDENTES[@]}" | xargs -P "$PARALELO" -I{} \
    curl -sS --fail --retry 3 --retry-delay 5 -o "$PASTA/{}" "$BASE/$SNAPSHOT/{}"
fi

FALHAS=0
for nome in "${ARQUIVOS[@]}"; do
  if ! unzip -tqq "$PASTA/$nome" >/dev/null 2>&1; then
    echo "[erro] zip inválido/incompleto: $PASTA/$nome (rode de novo pra retomar)" >&2
    FALHAS=1
  fi
done
(( FALHAS )) && exit 1
echo "OK: ${#ARQUIVOS[@]} arquivos em $PASTA" >&2
