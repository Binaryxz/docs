#!/usr/bin/env bash
# Etapa: Circheck / circom --inspect — análise estática de sinais e entradas sem restrição
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 21-circheck "Circheck + circom --inspect"

inv_vazio circom && pular "nenhum circuito Circom encontrado"
inv_vazio circom_main && pular "nenhum circuito com 'component main'"
tem circom || tem circheck || pular "nem circom nem circheck instalados"

libs=(-l "$REPO_DIR" -l "$REPO_DIR/node_modules" -l "$REPO_DIR/node_modules/circomlib/circuits")
avisos=0 falhas_ch=0
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
    # Pelo wrapper: sem ele o Circheck não analisa circuitos com o Poseidon do circomlib
    # (recursão, componente usado antes de criado, atribuição de array inteiro).
    timeout 1800 python3 "$PIPELINE_DIR/lib/circheck_run.py" "$src" --json "$SDIR/$nome.circheck.json" > "$SDIR/$nome.circheck.log" 2>&1 || true
    grep -q 'Analysis failed' "$SDIR/$nome.circheck.log" && falhas_ch=$((falhas_ch + 1))
    n=$(grep -oE 'Total warnings: [0-9]+' "$SDIR/$nome.circheck.log" | grep -oE '[0-9]+$' | tail -1) || true
    avisos=$((avisos + ${n:-0}))
  fi
done < "$INV_DIR/circom_main.txt"

nota=""; tem circheck || nota=" (binário circheck ausente — só circom --inspect)"
[ "$falhas_ch" -gt 0 ] && nota+=" (Circheck falhou em $falhas_ch; veja os .circheck.log)"
concluir "$(wc -l < "$INV_DIR/circom_main.txt") circuito(s) inspecionado(s)$nota" "$avisos"
