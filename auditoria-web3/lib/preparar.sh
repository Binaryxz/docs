#!/usr/bin/env bash
# Instala o que um repositório precisa para compilar quando chega sem dependências
# (zip do GitHub, clone sem submódulos): pacotes npm e libs do Foundry em lib/.
#
#   preparar.sh <repo_dir> <log>
#
# O código auditado não é confiável: npm roda com --ignore-scripts, e nada aqui
# executa scripts do projeto. Falhas só são registradas; quem precisa compilar
# relata o erro na própria etapa.

set -uo pipefail
REPO="$1" LOG="$2"
exec >>"$LOG" 2>&1
echo "== preparo de dependências: $(date -u +%FT%TZ)"

if [ "${AUDITAR_SEM_DEPENDENCIAS:-0}" = 1 ]; then
  echo "AUDITAR_SEM_DEPENDENCIAS=1: nada instalado"; exit 0
fi

# 1. npm na raiz (workspaces instalam os pacotes internos também).
if [ -f "$REPO/package.json" ] && [ ! -d "$REPO/node_modules" ] && command -v npm >/dev/null; then
  echo "-- npm (--ignore-scripts)"
  (cd "$REPO" && {
      # npm ci exige lock em sincronia com o package.json; se não estiver, registra e instala mesmo assim.
      { [ -f package-lock.json ] && timeout "${NPM_TIMEOUT:-900}" npm ci --ignore-scripts --no-audit --no-fund; } \
        || { [ -f package-lock.json ] && echo "AVISO: npm ci recusou (package-lock.json fora de sincronia?); usando npm install"; \
             timeout "${NPM_TIMEOUT:-900}" npm install --ignore-scripts --no-audit --no-fund; }
    }) || echo "AVISO: instalação npm falhou"
fi

# 2. Submódulos declarados (clone raso; um zip não traz o conteúdo deles).
if [ -f "$REPO/.gitmodules" ]; then
  git config -f "$REPO/.gitmodules" --get-regexp '^submodule\..*\.path$' | while read -r chave caminho; do
    url=$(git config -f "$REPO/.gitmodules" --get "${chave%.path}.url")
    if [ -n "$url" ] && [ ! -e "$REPO/$caminho/.git" ] && [ -z "$(ls -A "$REPO/$caminho" 2>/dev/null)" ]; then
      echo "-- submódulo $caminho <- $url"
      timeout 300 git clone --quiet --depth 1 "$url" "$REPO/$caminho" || echo "AVISO: falha ao clonar $url"
    fi
  done
fi

# 3. Remapeamentos do Foundry que apontam para lib/<nome> inexistente: liga ao pacote
#    npm equivalente (node_modules/<nome>, node_modules/@*/<nome>, node_modules/@*/<nome>.sol).
find "$REPO" -name foundry.toml -not -path '*/node_modules/*' -not -path '*/lib/*' | while read -r toml; do
  dir=$(dirname "$toml")
  { grep -hoE '"[^"=]+=[^"]+"' "$toml" | tr -d '"'; cat "$dir/remappings.txt" 2>/dev/null; } \
    | grep -oE '=lib/[^/]+' | cut -d/ -f2 | sort -u | while read -r nome; do
      [ -e "$dir/lib/$nome" ] && continue
      alvo=""
      # O pacote Solidity (nome.sol) vem antes do homônimo JS, em qualquer node_modules
      # (@zk-kit/lean-imt.sol, não @zk-kit/lean-imt); e só vale um diretório com .sol.
      for padrao in "%s.sol" "@*/%s.sol" "%s" "@*/%s"; do
        for nm in "$dir/node_modules" "$REPO/node_modules"; do
          for c in $nm/$(printf "$padrao" "$nome"); do
            if [ -d "$c" ] && find -L "$c" -maxdepth 3 -name '*.sol' -print -quit | grep -q .; then
              alvo="$c"; break 3
            fi
          done
        done
      done
      if [ -n "$alvo" ]; then
        mkdir -p "$dir/lib" && ln -s "$alvo" "$dir/lib/$nome" && echo "-- lib/$nome -> $alvo (em ${dir#$REPO/})"
      else
        echo "AVISO: lib/$nome não encontrado (em ${dir#$REPO/})"
      fi
    done
done
echo "== fim do preparo"
