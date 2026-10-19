#!/usr/bin/env bash
# Etapa: privacy-protocol-auditor — nullifiers, commitments, ownership, front-running
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 31-privacidade "privacy-protocol-auditor"

inv_vazio privacidade && pular "nenhuma lógica de shielded pool/mixer encontrada"

# Se você baixou a skill original (SKILL.md), ela é anexada ao prompt.
ctx=""
if [ -n "${PRIVACY_SKILL_PATH:-}" ] && [ -f "$PRIVACY_SKILL_PATH" ]; then
  ctx="## Metodologia adicional (skill privacy-protocol-auditor)
$(cat "$PRIVACY_SKILL_PATH")"
fi
etapa_llm privacidade privacidade "" "" "$ctx"
