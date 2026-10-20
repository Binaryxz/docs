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
    # Harnesses montados por herança (FuzzTest is PropertiesParent is ...): o alvo é o
    # contrato folha que herda as propriedades, não os pais que as definem.
    while read -r r; do
      folhas=$(python3 -I "$PIPELINE_DIR/lib/medusa_alvos.py" "$r")
      [ -n "$folhas" ] && alvos[$r]="$folhas"
    done < <(grep -rlIE --include='*.sol' "${EXC[@]}" 'function (property_|echidna_)' . 2>/dev/null \
              | sed 's|^\./||' | while read -r f; do raiz_de "$f"; done | sort -u)
  fi
  for r in "${!alvos[@]}"; do
    nome=$(echo "$r" | tr '/.' '__')
    log "medusa em $r: ${alvos[$r]}"
    cfg=()
    if [ -f "$r/medusa.json" ]; then
      cfg=(--config medusa.json)
    else
      # Sem config do projeto: liga também o modo de asserções, porque harnesses no estilo
      # property_x(uint256 seed) usam assert/require em vez de retornar bool.
      tmpd=$(mktemp -d); tmpcfg="$tmpd/medusa.json"
      if (cd "$tmpd" && medusa init --out medusa.json) >/dev/null 2>&1 \
         && jq --arg alvo "$REPO_DIR/$r" '.compilation.platformConfig.target = $alvo
              | .fuzzing.testing.assertionTesting.enabled = true
              | .fuzzing.testing.propertyTesting.enabled = true
              | .fuzzing.testing.propertyTesting.testPrefixes = ["property_", "echidna_"]
              | .fuzzing.codeSizeCheckDisabled = true' "$tmpcfg" > "$tmpcfg.novo"; then
        mv "$tmpcfg.novo" "$tmpcfg"; cfg=(--config "$tmpcfg")
      fi
    fi
    (cd "$r" && timeout $(( ${MEDUSA_TIMEOUT:-900} + 300 )) medusa fuzz "${cfg[@]}" \
        --target-contracts "${alvos[$r]}" --timeout "${MEDUSA_TIMEOUT:-900}" \
        --workers "${MEDUSA_WORKERS:-$(nproc)}") > "$SDIR/medusa-$nome.log" 2>&1
    n=$(grep -cE '\[FAILED\]' "$SDIR/medusa-$nome.log") || true
    if grep -qiE 'compilation (failed|error)|CryticCompile:Compilation failed' "$SDIR/medusa-$nome.log"; then
      resumo+="medusa $r: falha de compilação; "
    elif grep -qiE 'Failed to start fuzzer|no assertion, property, optimization' "$SDIR/medusa-$nome.log"; then
      resumo+="medusa $r: não fuzzou ($(grep -oiE 'no assertion[^[:cntrl:]]*|Failed to start fuzzer' "$SDIR/medusa-$nome.log" | tail -1)); "
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
    # O solc para no primeiro arquivo com erro: repete acumulando os quebrados (até 10 voltas).
    quebrados=""
    for _volta in $(seq 1 10); do
      grep -qE '^\[(PASS|FAIL)' "$log_f" && break
      novos=$(grep -oE -- '--> [^:]+\.t\.sol' "$log_f" | awk '{print $2}' | sort -u | grep -vxF -f <(echo "$quebrados") || true)
      [ -n "$novos" ] || break
      quebrados=$(printf '%s\n%s\n' "$quebrados" "$novos" | grep . | sort -u)
      skip=(); for q in $quebrados; do skip+=(--skip "$q"); done
      log "forge em $r: pulando testes que não compilam: $(echo $quebrados | tr '\n' ' ')"
      (cd "$r" && timeout "${FORGE_TIMEOUT:-1200}" forge test --match-test 'invariant' \
          --fuzz-runs "${FORGE_RUNS:-256}" -vv "${skip[@]}") > "$log_f" 2>&1
      rc=$?
    done
    if [ -n "$quebrados" ]; then
      echo "$quebrados" > "$SDIR/testes-que-nao-compilam-$nome.txt"
      resumo+="forge $r: $(echo "$quebrados" | wc -l) teste(s) do projeto não compilam (pulados: $(echo $quebrados | tr '\n' ' ')); "
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
