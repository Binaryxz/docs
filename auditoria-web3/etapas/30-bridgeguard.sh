#!/usr/bin/env bash
# Etapa: BridgeGuard MCP — 18+ padrões de vulnerabilidade em bridges (inclui Hyperliquid)
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 30-bridgeguard "BridgeGuard MCP"

inv_vazio bridge && pular "nenhum código de bridge/cross-chain encontrado"
tem npx || pular "npx não instalado (BridgeGuard roda via npm)"

jq -n '{mcpServers:{bridgeguard:{command:"npx", args:["-y","bridgeguard-mcp@0.1.1"]}}}' > "$SDIR/mcp.json"
etapa_llm bridge bridge "$SDIR/mcp.json" "mcp__bridgeguard"
