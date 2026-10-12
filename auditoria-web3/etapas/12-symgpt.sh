#!/usr/bin/env bash
# Etapa: SymGPT — execução simbólica + LLM para conformidade com regras ERC
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 12-symgpt SymGPT

inv_vazio erc && pular "nenhum contrato ERC (20/721/1155/4626...) encontrado"
SYMGPT_HOME="${SYMGPT_HOME:-/opt/symgpt}"
[ -x "$SYMGPT_HOME/x" ] || pular "SymGPT não instalado em $SYMGPT_HOME"
[ -n "${OPENAI_API_KEY:-}" ] || pular "OPENAI_API_KEY não definida (SymGPT usa a API da OpenAI)"

max="${SYMGPT_MAX_ARQUIVOS:-20}" total=0 feitos=0
while read -r f && [ "$feitos" -lt "$max" ]; do
  nome=$(echo "$f" | tr '/.' '__')
  log "auditando $f"
  (cd "$SYMGPT_HOME" && timeout "${SYMGPT_TIMEOUT:-1200}" ./x audit "$REPO_DIR/$f" \
      --out-dir "$SDIR/$nome") > "$SDIR/$nome.log" 2>&1 || true
  n=$(find "$SDIR/$nome" -name '*.json' -exec jq '[.. | objects | select(has("rule") and has("erc"))] | length' {} + 2>/dev/null \
      | awk '{s+=$1} END {print s+0}')
  total=$((total + n)); feitos=$((feitos + 1))
done < "$INV_DIR/erc.txt"

concluir "$feitos contrato(s) ERC verificados (limite $max)" "$total"
