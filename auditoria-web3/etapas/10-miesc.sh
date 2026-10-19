#!/usr/bin/env bash
# Etapa: MIESC — orquestra Slither, Mythril e demais analisadores sobre os contratos
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 10-miesc MIESC

inv_vazio solidity && pular "nenhum contrato Solidity encontrado"
tem miesc || pular "miesc não instalado (pip install miesc[full])"

total=0 erros=0
while read -r raiz; do
  nome=$(echo "$raiz" | tr '/.' '__')
  log "varrendo $raiz"
  if (cd "$REPO_DIR" && timeout "${MIESC_TIMEOUT:-3600}" miesc scan "$raiz" --recursive \
        -o "$SDIR/resultado-$nome.json") > "$SDIR/miesc-$nome.log" 2>&1; then
    n=$(jq '.total_findings // (.findings | length) // 0' "$SDIR/resultado-$nome.json" 2>/dev/null || echo 0)
    total=$((total + n))
  else
    erros=$((erros + 1))
  fi
done < "$INV_DIR/sol_raizes.txt"

[ "$erros" -gt 0 ] && [ "$total" -eq 0 ] && falhar "miesc falhou em $erros diretório(s); veja os .log"
concluir "varredura concluída em $(wc -l < "$INV_DIR/sol_raizes.txt") diretório(s)" "$total"
