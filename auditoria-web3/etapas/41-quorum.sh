#!/usr/bin/env bash
# Etapa: Quorum (Certora) — valida payloads de propostas de governança já deployados
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 41-quorum "Quorum (Certora)"

[ -n "${QUORUM_PAYLOADS:-}" ] || pular "QUORUM_PAYLOADS não definido (endereços de payloads de proposta a validar)"
tem quorum || pular "quorum não instalado (pip install certora-quorum)"
[ -n "${ETHSCAN_API_KEY:-}" ] || pular "ETHSCAN_API_KEY não definida (exigida pelo Quorum)"

export QUORUM_PATH="${QUORUM_PATH:-$SDIR/artefatos}"
mkdir -p "$QUORUM_PATH"
[ -f "$QUORUM_PATH/ground_truth.json" ] || quorum setup --working-dir "$QUORUM_PATH" > "$SDIR/setup.log" 2>&1

ok=0 erros=0
for p in ${QUORUM_PAYLOADS//,/ }; do
  log "validando $p"
  if timeout 1800 quorum validate-address --protocol-name "${QUORUM_PROTOCOLO:?defina QUORUM_PROTOCOLO}" \
       --chain "${QUORUM_REDE:-Ethereum}" --payload-address "$p" > "$SDIR/$p.log" 2>&1; then
    ok=$((ok + 1))
  else
    erros=$((erros + 1))
  fi
done
[ "$ok" -eq 0 ] && falhar "nenhum payload validado; veja os .log"
concluir "$ok payload(s) validados, $erros com erro — artefatos em $QUORUM_PATH"
