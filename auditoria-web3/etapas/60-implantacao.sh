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

# Recompila o repositório e compara cada endereço (rótulo = nome do contrato) com o build.
cmp=""
if tem forge && [ -s "$SDIR/estado.json" ]; then
  dirs=()
  while read -r toml; do
    r=$(dirname "$toml")
    log "forge build em ${r#$REPO_DIR/}"
    (cd "$r" && FOUNDRY_DYNAMIC_TEST_LINKING=false timeout 1800 forge build --skip test --skip script) \
      >> "$SDIR/forge-build.log" 2>&1 || log "forge build falhou em ${r#$REPO_DIR/} (veja forge-build.log)"
    for o in out forge-out; do [ -d "$r/$o" ] && dirs+=("$r/$o"); done
  done < <(find "$REPO_DIR" -name foundry.toml -not -path '*/node_modules/*' -not -path '*/lib/*')
  if [ ${#dirs[@]} -gt 0 ]; then
    cmp=$(python3 -I "$PIPELINE_DIR/lib/bytecode_cmp.py" "$SDIR/estado.json" "$SDIR/bytecode.json" "${dirs[@]}" 2>&1 | tail -1)
  fi
fi

n=$(jq '.contratos | length' "$SDIR/estado.json")
sem_codigo=$(jq '[.contratos[] | select(.bytes == 0)] | length' "$SDIR/estado.json")
eoa_gov=$(jq -r '[.contratos[] | .getters["governor()"] // empty] | unique | join(", ")' "$SDIR/estado.json")
concluir "$n endereço(s) lidos no bloco $(jq .bloco "$SDIR/estado.json"); $sem_codigo sem código; governors: ${eoa_gov:-nenhum}${cmp:+; bytecode: $cmp}" "$sem_codigo"
