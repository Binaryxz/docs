#!/usr/bin/env bash
# Etapa: Medusa — fuzzing stateful de invariantes (property_*, assertions)
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 13-medusa Medusa

inv_vazio solidity && pular "nenhum contrato Solidity encontrado"
tem medusa || pular "medusa não instalado (go install github.com/crytic/medusa@latest)"
TIPO=$(cat "$INV_DIR/projeto.txt")
[ "$TIPO" = nenhum ] && pular "sem foundry.toml/hardhat.config — medusa precisa compilar o projeto"

cd "$REPO_DIR" || falhar "repositório inacessível"

# Alvos: MEDUSA_ALVOS explícito ou contratos que declaram propriedades de fuzzing.
alvos="${MEDUSA_ALVOS:-}"
if [ -z "$alvos" ]; then
  alvos=$(grep -rlIE --include='*.sol' --exclude-dir=node_modules --exclude-dir=lib \
            'function (property_|echidna_|invariant_)' . 2>/dev/null \
          | xargs -r grep -hoE '^\s*contract\s+[A-Za-z0-9_]+' | awk '{print $2}' | sort -u | paste -sd, -)
fi
[ -n "$alvos" ] || pular "nenhum harness de invariantes (property_*/echidna_*/invariant_*); defina MEDUSA_ALVOS ou escreva propriedades"

# Dependências: sem scripts de instalação (o repositório auditado não é confiável).
if [ "$TIPO" = hardhat ] && [ -f package.json ] && [ ! -d node_modules ]; then
  log "instalando dependências npm (--ignore-scripts)"
  { npm ci --ignore-scripts || npm install --ignore-scripts; } > "$SDIR/npm.log" 2>&1
fi

cfg=()
[ -f medusa.json ] && cfg=(--config medusa.json)
log "alvos: $alvos"
timeout $(( ${MEDUSA_TIMEOUT:-900} + 300 )) medusa fuzz "${cfg[@]}" \
  --target-contracts "$alvos" --timeout "${MEDUSA_TIMEOUT:-900}" \
  --workers "${MEDUSA_WORKERS:-$(nproc)}" > "$SDIR/medusa.log" 2>&1
rc=$?

falhas=$(grep -cE '\[FAILED\]' "$SDIR/medusa.log" 2>/dev/null) || true
falhas=${falhas:-0}
grep -qiE 'compilation (failed|error)|error while compiling' "$SDIR/medusa.log" && falhar "falha de compilação; veja medusa.log"
[ "$rc" -ne 0 ] && [ "$falhas" -eq 0 ] && falhar "medusa saiu com código $rc; veja medusa.log"
concluir "fuzzing de $alvos por ${MEDUSA_TIMEOUT:-900}s — $falhas invariante(s) quebrada(s)" "$falhas"
