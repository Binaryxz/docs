#!/usr/bin/env bash
# Etapa: Circheck / circom --inspect — análise estática de sinais e entradas sem restrição
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 21-circheck "Circheck + circom --inspect"

inv_vazio circom && pular "nenhum circuito Circom encontrado"
inv_vazio circom_main && pular "nenhum circuito com 'component main'"
tem circom || tem circheck || pular "nem circom nem circheck instalados"

libs=(-l "$REPO_DIR" -l "$REPO_DIR/node_modules" -l "$REPO_DIR/node_modules/circomlib/circuits")
avisos=0
while read -r f; do
  # Fontes do repositório vêm relativos; os `main` gerados do circomkit, absolutos.
  [[ "$f" == /* ]] && src="$f" || src="$REPO_DIR/$f"
  nome=$(basename "$f" .circom | tr '/.' '__')
  if tem circom; then
    tmp=$(mktemp -d)
    (cd "$(dirname "$src")" && timeout 600 circom "$src" --r1cs --inspect "${libs[@]}" -o "$tmp") \
      > "$SDIR/$nome.inspect.log" 2>&1
    rm -rf "$tmp"
    n=$(grep -ciE '^warning|underconstrained|not constrained' "$SDIR/$nome.inspect.log") || true
    avisos=$((avisos + ${n:-0}))
  fi
  if tem circheck; then
    timeout 600 circheck "$src" > "$SDIR/$nome.circheck.log" 2>&1 || true
    n=$(grep -ciE 'warning|unconstrained|division' "$SDIR/$nome.circheck.log") || true
    avisos=$((avisos + ${n:-0}))
  fi
done < "$INV_DIR/circom_main.txt"

nota=""; tem circheck || nota=" (binário circheck ausente — só circom --inspect)"
concluir "$(wc -l < "$INV_DIR/circom_main.txt") circuito(s) inspecionado(s)$nota" "$avisos"
