#!/usr/bin/env bash
# Etapa: Anticapture — riscos de captura de governança (código + dados on-chain via MCP)
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 40-governanca "Anticapture"

inv_vazio governanca && pular "nenhum contrato de governança (Governor, Timelock, ERC20Votes) encontrado"

mcp="" extras="" ctx=""
# A config leva a chave da API: fica fora da pasta de resultados (que vira artefato).
mcp_tmp=$(umask 077 && mktemp)
trap 'rm -f "$mcp_tmp"' EXIT
if [ -n "${ANTICAPTURE_MCP_API_KEY:-}" ]; then
  jq -n --arg k "Bearer $ANTICAPTURE_MCP_API_KEY" \
    '{mcpServers:{anticapture:{type:"http", url:"https://mcp.anticapture.com/mcp", headers:{Authorization:$k}}}}' \
    > "$mcp_tmp"
  mcp="$mcp_tmp" extras="mcp__anticapture"
fi
[ -n "${DAO_ID:-}${DAO_ADDRESS:-}" ] && ctx="DAO a consultar: ${DAO_ID:-} ${DAO_ADDRESS:-} (rede: ${DAO_CHAIN:-ethereum})"

etapa_llm governanca governanca "$mcp" "$extras" "$ctx"
