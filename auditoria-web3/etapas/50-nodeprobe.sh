#!/usr/bin/env bash
# Etapa: Nodeprobe — scan de websites, RPC/GraphQL e contratos deployados (relayers, attestors, keepers)
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 50-nodeprobe Nodeprobe

# URLs encontradas no código só são listadas, nunca escaneadas automaticamente:
# só escaneie infraestrutura que você opera ou tem autorização para testar.
cp "$INV_DIR/urls_rpc.txt" "$SDIR/urls_encontradas_no_codigo.txt" 2>/dev/null || true

[ -n "${NODEPROBE_WEB:-}${NODEPROBE_RPC:-}${NODEPROBE_CONTRATOS:-}" ] \
  || pular "nenhum alvo autorizado (NODEPROBE_WEB / NODEPROBE_RPC / NODEPROBE_CONTRATOS); $(wc -l < "$SDIR/urls_encontradas_no_codigo.txt" 2>/dev/null || echo 0) URL(s) RPC listadas para revisão"
tem nodeprobe || pular "nodeprobe não instalado (pipx install nodeprobe)"

perfil=(--profile "${NODEPROBE_PERFIL:-Standard}")
n=0
for u in ${NODEPROBE_WEB//,/ }; do
  n=$((n+1)); nodeprobe web "$u" "${perfil[@]}" --json --pretty > "$SDIR/web-$n.json" 2> "$SDIR/web-$n.log" || true
done
for u in ${NODEPROBE_RPC//,/ }; do
  n=$((n+1)); nodeprobe scan "$u" "${perfil[@]}" --json --pretty > "$SDIR/rpc-$n.json" 2> "$SDIR/rpc-$n.log" || true
done
for c in ${NODEPROBE_CONTRATOS//,/ }; do
  [ -n "${NODEPROBE_CONTRATO_RPC:-}" ] || { log "NODEPROBE_CONTRATO_RPC não definido; pulando $c"; continue; }
  n=$((n+1)); nodeprobe contract "$c" --rpc "$NODEPROBE_CONTRATO_RPC" --chain "${NODEPROBE_CHAIN_ID:-1}" \
    "${perfil[@]}" --json --pretty > "$SDIR/contrato-$n.json" 2> "$SDIR/contrato-$n.log" || true
done
concluir "$n alvo(s) escaneado(s)"
