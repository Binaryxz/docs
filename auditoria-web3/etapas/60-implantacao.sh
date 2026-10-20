#!/usr/bin/env bash
# Etapa: contratos implantados — estado on-chain (papéis, Safes, proxies, saldos), só leitura
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 60-implantacao "Contratos implantados (on-chain)"

[ -n "${ONCHAIN_ENDERECOS:-}" ] || pular "ONCHAIN_ENDERECOS não definido (endereço[=rótulo] separados por espaço ou vírgula)"
[ -n "${ONCHAIN_RPC:-}" ] || pular "ONCHAIN_RPC não definido (URL JSON-RPC da rede dos contratos)"
python3 -c "import web3" 2>/dev/null || pular "pacote web3 ausente"

# shellcheck disable=SC2086
TOKENS="${ONCHAIN_TOKENS:-}" timeout 1800 python3 -I "$PIPELINE_DIR/lib/onchain.py" "$ONCHAIN_RPC" "$SDIR/estado.json" \
  $(echo "$ONCHAIN_ENDERECOS" | tr ',' ' ') > "$SDIR/onchain.log" 2>&1 || falhar "leitura on-chain falhou: $(tail -1 "$SDIR/onchain.log")"

n=$(jq '.contratos | length' "$SDIR/estado.json")
sem_codigo=$(jq '[.contratos[] | select(.bytes == 0)] | length' "$SDIR/estado.json")
eoa_gov=$(jq -r '[.contratos[] | .getters["governor()"] // empty] | unique | join(", ")' "$SDIR/estado.json")
concluir "$n endereço(s) lidos no bloco $(jq .bloco "$SDIR/estado.json"); $sem_codigo sem código; governors: ${eoa_gov:-nenhum}" "$sem_codigo"
