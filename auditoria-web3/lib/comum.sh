#!/usr/bin/env bash
# Funções compartilhadas pelas etapas. Cada etapa é executada como processo
# separado com REPO_DIR, OUT_DIR e INV_DIR já exportados por auditar.sh.

set -uo pipefail

PIPELINE_DIR="${PIPELINE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

tem() { command -v "$1" >/dev/null 2>&1; }

log() { printf '[%s] %s\n' "${ETAPA:-pipeline}" "$*" >&2; }

# etapa_inicio <id> <ferramenta>
etapa_inicio() {
  ETAPA="$1"
  FERRAMENTA="$2"
  SDIR="$OUT_DIR/$ETAPA"
  mkdir -p "$SDIR"
  T0=$(date +%s)
  log "iniciando ($FERRAMENTA)"
}

_status() {
  local status="$1" msg="$2" achados="${3:-null}"
  jq -n --arg etapa "$ETAPA" --arg ferramenta "$FERRAMENTA" \
    --arg status "$status" --arg mensagem "$msg" \
    --argjson achados "$achados" --argjson duracao "$(( $(date +%s) - T0 ))" \
    '{etapa:$etapa, ferramenta:$ferramenta, status:$status, mensagem:$mensagem, achados:$achados, duracao_s:$duracao}' \
    > "$SDIR/status.json"
  log "$status: $msg"
}

# Uma etapa nunca derruba o pipeline: sempre sai com 0.
pular() { _status pulada "$1"; exit 0; }
concluir() { _status ok "$1" "${2:-null}"; exit 0; }
falhar() { _status erro "$1" "${2:-null}"; exit 0; }

# inv <nome>: lista de arquivos do inventário (uma linha por caminho).
inv() { cat "$INV_DIR/$1.txt" 2>/dev/null || true; }
inv_vazio() { [ ! -s "$INV_DIR/$1.txt" ]; }

# Revisão por LLM com Claude Code em modo headless. O repositório auditado é
# conteúdo não confiável: só ferramentas de leitura são liberadas.
# revisao_llm <prompt.md> <saida.md> [mcp-config.json] [ferramentas extras]
revisao_llm() {
  local prompt="$1" saida="$2" mcp="${3:-}" extras="${4:-}"
  tem claude || { echo "Claude Code CLI não instalado"; return 2; }
  [ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "ANTHROPIC_API_KEY não definida"; return 2; }

  local args=(-p --output-format text --allowedTools "Read Grep Glob $extras"
              --disallowedTools "Bash Edit Write NotebookEdit WebFetch WebSearch")
  [ -n "$mcp" ] && args+=(--mcp-config "$mcp" --strict-mcp-config)
  [ -n "${LLM_MODEL:-}" ] && args+=(--model "$LLM_MODEL")

  (cd "$REPO_DIR" && timeout "${LLM_TIMEOUT:-1800}" claude "${args[@]}" < "$prompt") > "$saida" 2> "$saida.log"
}

# Conta os achados de severidade no relatório LLM (linhas "### [ALTA] ...").
contar_achados_md() {
  local n
  n=$(grep -cE '^#{2,4} *\[(CR[IÍ]TICA|ALTA|M[EÉ]DIA|BAIXA)\]' "$1" 2>/dev/null) || true
  echo "${n:-0}"
}

# Monta o prompt (base + lista de arquivos + contexto + formato) e roda a revisão.
# etapa_llm <prompt-base> <inventário> [mcp-config] [ferramentas extras] [contexto extra]
etapa_llm() {
  local base="$1" lista="$2" mcp="${3:-}" extras="${4:-}" ctx="${5:-}"
  local prompt="$SDIR/prompt.md"
  {
    cat "$PIPELINE_DIR/prompts/$base.md"
    inv "$lista" | head -n "${LLM_MAX_ARQUIVOS:-200}" | sed 's/^/- /'
    [ -n "$ctx" ] && printf '\n%s\n' "$ctx"
    cat "$PIPELINE_DIR/prompts/_formato.md"
  } > "$prompt"

  local msg rc
  msg=$(revisao_llm "$prompt" "$SDIR/relatorio.md" "$mcp" "$extras"); rc=$?
  [ "$rc" -eq 2 ] && pular "$msg"
  if [ "$rc" -ne 0 ]; then
    # O CLI escreve o erro da API (ex.: crédito insuficiente) na saída, não no stderr.
    local motivo
    motivo=$(head -c 200 "$SDIR/relatorio.md" "$SDIR/relatorio.md.log" 2>/dev/null | tr '\n' ' ')
    falhar "revisão LLM falhou (código $rc): ${motivo:-veja relatorio.md.log}"
  fi
  concluir "revisão concluída — $(contar_achados_md "$SDIR/relatorio.md") achado(s)" "$(contar_achados_md "$SDIR/relatorio.md")"
}
