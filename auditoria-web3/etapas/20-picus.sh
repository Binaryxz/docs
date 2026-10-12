#!/usr/bin/env bash
# Etapa: Picus (Veridise) — verificação formal de circuitos Circom sub-restritos
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 20-picus Picus

inv_vazio circom && pular "nenhum circuito Circom encontrado"
inv_vazio circom_main && pular "nenhum circuito com 'component main' (Picus precisa do componente principal)"
PICUS="${PICUS_BIN:-$(command -v run-picus || echo /opt/Picus/run-picus)}"
[ -x "$PICUS" ] || pular "Picus não instalado"
tem circom || pular "circom não instalado (Picus o usa para compilar)"

inseguros=0 seguros=0 desconhecidos=0
while read -r f; do
  nome=$(echo "$f" | tr '/.' '__')
  log "verificando $f"
  (cd "$(dirname "$REPO_DIR/$f")" && timeout "${PICUS_TIMEOUT_TOTAL:-1800}" "$PICUS" \
      --solver "${PICUS_SOLVER:-cvc5}" --timeout "${PICUS_SMT_TIMEOUT:-5000}" \
      --json "$SDIR/$nome.json" "$REPO_DIR/$f") > "$SDIR/$nome.log" 2>&1
  if grep -qiE 'unsafe|underconstrained' "$SDIR/$nome.log"; then
    inseguros=$((inseguros + 1)); echo "$f" >> "$SDIR/inseguros.txt"
  elif grep -qiE '\bsafe\b|properly constrained' "$SDIR/$nome.log"; then
    seguros=$((seguros + 1))
  else
    desconhecidos=$((desconhecidos + 1))
  fi
done < "$INV_DIR/circom_main.txt"

concluir "$seguros seguro(s), $inseguros sub-restrito(s), $desconhecidos inconclusivo(s)" "$inseguros"
