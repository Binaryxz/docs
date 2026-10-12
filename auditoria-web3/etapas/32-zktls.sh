#!/usr/bin/env bash
# Etapa: TLSNotary + Reclaim — revisão das provas zkTLS de pagamento (PIX/boleto) e escrow
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 32-zktls "TLSNotary / Reclaim Protocol"

inv_vazio zktls && pular "nenhuma integração zkTLS (tlsn, Reclaim, attestor-core) encontrada"
etapa_llm zktls zktls
