#!/usr/bin/env bash
# Etapa: SolidityGuard — 104 padrões de vulnerabilidade + ferramentas integradas
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 11-solidityguard SolidityGuard

inv_vazio solidity && pular "nenhum contrato Solidity encontrado"
SG_HOME="${SOLIDITYGUARD_HOME:-/opt/SolidityGuard}"
tem solidityguard || [ -f "$SG_HOME/scripts/solidity_guard.py" ] || pular "SolidityGuard não instalado"

total=0
while read -r raiz; do
  nome=$(echo "$raiz" | tr '/.' '__')
  if [ -f "$SG_HOME/scripts/solidity_guard.py" ]; then
    (cd "$REPO_DIR" && timeout "${SG_TIMEOUT:-1800}" python3 "$SG_HOME/scripts/solidity_guard.py" \
        scan --path "$raiz" --json) > "$SDIR/achados-$nome.json" 2> "$SDIR/scan-$nome.log"
    n=$(jq '[.. | objects | select(has("severity"))] | length' "$SDIR/achados-$nome.json" 2>/dev/null || echo 0)
    total=$((total + n))
  fi
  if tem solidityguard; then
    (cd "$REPO_DIR" && timeout "${SG_TIMEOUT:-1800}" solidityguard audit "$raiz") \
      > "$SDIR/auditoria-$nome.txt" 2>&1 || true
    # Sem JSON do scanner, o total vem do rodapé do CLI ("Score: N/100 | X findings").
    if [ ! -s "$SDIR/achados-$nome.json" ]; then
      n=$(grep -oE '[0-9]+ findings' "$SDIR/auditoria-$nome.txt" | tail -1 | grep -oE '^[0-9]+') || true
      total=$((total + ${n:-0}))
    fi
  fi
done < "$INV_DIR/sol_raizes.txt"

concluir "padrões verificados em $(wc -l < "$INV_DIR/sol_raizes.txt") diretório(s)" "$total"
