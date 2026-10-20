#!/usr/bin/env bash
# Etapa: Medusa — fuzzing stateful de invariantes (property_*, echidna_*) + invariantes do forge
. "$PIPELINE_DIR/lib/comum.sh"
etapa_inicio 13-medusa Medusa

inv_vazio solidity && pular "nenhum contrato Solidity encontrado"
tem medusa || tem forge || pular "nem medusa nem forge instalados"
cd "$REPO_DIR" || falhar "repositório inacessível"

# Raiz Foundry/Hardhat mais próxima de um arquivo (monorepos têm várias).
raiz_de() {
  local d; d=$(dirname "$1")
  while [ "$d" != "." ] && [ "$d" != "/" ]; do
    [ -f "$d/foundry.toml" ] || ls "$d"/hardhat.config.* >/dev/null 2>&1 && { echo "$d"; return; }
    d=$(dirname "$d")
  done
  [ -f foundry.toml ] || ls hardhat.config.* >/dev/null 2>&1 && echo "."
}

EXC=(--exclude-dir=node_modules --exclude-dir=lib --exclude-dir=forge-out --exclude-dir=out)
falhas=0 rodadas=0 resumo=""

# 1. Medusa sobre harnesses estilo Echidna/Medusa, agrupados por projeto.
if tem medusa; then
  declare -A alvos=()
  if [ -n "${MEDUSA_ALVOS:-}" ]; then
    alvos["${MEDUSA_RAIZ:-.}"]="$MEDUSA_ALVOS"
  else
    while read -r f; do
      r=$(raiz_de "$f"); [ -n "$r" ] || continue
      for c in $(grep -hoE '^\s*contract\s+[A-Za-z0-9_]+' "$f" | awk '{print $2}'); do
        case ",${alvos[$r]:-}," in *",$c,"*) ;; *) alvos[$r]="${alvos[$r]:+${alvos[$r]},}$c" ;; esac
      done
    done < <(grep -rlIE --include='*.sol' "${EXC[@]}" 'function (property_|echidna_)' . 2>/dev/null | sed 's|^\./||')
  fi
  for r in "${!alvos[@]}"; do
    # Só o contrato "folha" (não herdado por outro do projeto) é o harness: os pais
    # costumam ser abstratos e o Medusa não os implanta.
    herdados=$(grep -rhoIE --include='*.sol' "${EXC[@]}" '^\s*(abstract\s+)?contract\s+[A-Za-z0-9_]+\s+is\s+[^{]+' "$r" 2>/dev/null \
      | sed -E 's/.*\sis\s+//' | tr ',' '\n' | sed -E 's/\(.*//; s/\s//g' | sort -u)
    folhas=$(echo "${alvos[$r]}" | tr ',' '\n' | grep -vxF -f <(echo "$herdados") | paste -sd, -)
    [ -n "$folhas" ] && alvos[$r]="$folhas"
    nome=$(echo "$r" | tr '/.' '__')
    log "medusa em $r: ${alvos[$r]}"
    cfg=(); [ -f "$r/medusa.json" ] && cfg=(--config medusa.json)
    (cd "$r" && timeout $(( ${MEDUSA_TIMEOUT:-900} + 300 )) medusa fuzz "${cfg[@]}" \
        --target-contracts "${alvos[$r]}" --timeout "${MEDUSA_TIMEOUT:-900}" \
        --workers "${MEDUSA_WORKERS:-$(nproc)}") > "$SDIR/medusa-$nome.log" 2>&1
    n=$(grep -cE '\[FAILED\]' "$SDIR/medusa-$nome.log") || true
    if grep -qiE 'compilation (failed|error)|CryticCompile:Compilation failed' "$SDIR/medusa-$nome.log"; then
      resumo+="medusa $r: falha de compilação; "
    else
      rodadas=$((rodadas + 1)); falhas=$((falhas + ${n:-0}))
      resumo+="medusa $r: ${n:-0} propriedade(s) quebrada(s); "
    fi
  done
fi

# 2. Testes de invariantes do próprio projeto (forge invariant_*), por projeto Foundry.
if tem forge; then
  while read -r r; do
    nome=$(echo "$r" | tr '/.' '__')
    log "forge invariant em $r"
    log_f="$SDIR/forge-invariant-$nome.log"
    (cd "$r" && timeout "${FORGE_TIMEOUT:-1200}" forge test --match-test 'invariant' \
        --fuzz-runs "${FORGE_RUNS:-256}" -vv) > "$log_f" 2>&1
    rc=$?
    # Um teste do projeto que não compila derruba a suíte inteira: registra quais e
    # roda de novo pulando só esses arquivos.
    quebrados=$(grep -oE -- '--> [^:]+\.t\.sol' "$log_f" | awk '{print $2}' | sort -u)
    if [ -n "$quebrados" ] && ! grep -qE '^\[(PASS|FAIL)' "$log_f"; then
      echo "$quebrados" > "$SDIR/testes-que-nao-compilam-$nome.txt"
      skip=(); for q in $quebrados; do skip+=(--skip "$q"); done
      log "forge em $r: pulando testes que não compilam: $(echo $quebrados | tr '\n' ' ')"
      (cd "$r" && timeout "${FORGE_TIMEOUT:-1200}" forge test --match-test 'invariant' \
          --fuzz-runs "${FORGE_RUNS:-256}" -vv "${skip[@]}") > "$log_f" 2>&1
      rc=$?
      resumo+="forge $r: $(echo "$quebrados" | wc -l) teste(s) do projeto não compilam (pulados); "
    fi
    n=$(grep -cE '^\[FAIL' "$SDIR/forge-invariant-$nome.log") || true
    ok=$(grep -cE '^\[PASS' "$SDIR/forge-invariant-$nome.log") || true
    if [ "${ok:-0}" -eq 0 ] && [ "${n:-0}" -eq 0 ]; then
      resumo+="forge $r: sem execução (rc $rc, veja o log); "
    else
      rodadas=$((rodadas + 1)); falhas=$((falhas + ${n:-0}))
      resumo+="forge $r: ${ok:-0} ok, ${n:-0} falha(s); "
    fi
  done < <(grep -rlIE --include='*.sol' "${EXC[@]}" 'function invariant_' . 2>/dev/null \
            | sed 's|^\./||' | while read -r f; do r=$(raiz_de "$f"); [ -f "$r/foundry.toml" ] && echo "$r"; done | sort -u)
fi

[ -n "$resumo" ] || pular "nenhum harness de fuzzing (property_*/echidna_*) nem invariant_* encontrado; defina MEDUSA_ALVOS"
[ "$rodadas" -eq 0 ] && falhar "${resumo%; }"
concluir "${resumo%; }" "$falhas"
