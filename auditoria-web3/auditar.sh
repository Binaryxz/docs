#!/usr/bin/env bash
# Pipeline único de auditoria Web3.
#
#   ./auditar.sh https://github.com/org/repo [opções]
#
# Clona o repositório, faz o inventário (Solidity, Circom, bridges, governança,
# privacidade, zkTLS), roda só as etapas que se aplicam e gera RELATORIO.md.

set -uo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PIPELINE_DIR

uso() {
  cat <<'EOF'
Uso: auditar.sh <url-github | caminho-local> [opções]

Opções:
  --ref <branch|tag|commit>  Versão a auditar (padrão: branch padrão)
  --saida <dir>              Diretório de resultados (padrão: ./resultados/<repo>-<data>)
  --etapas <a,b,...>         Roda só estas etapas (ex: miesc,picus)
  --pular <a,b,...>          Pula estas etapas
  --config <arquivo.env>     Carrega variáveis (chaves de API, RPCs, DAO...)
  --listar                   Lista as etapas disponíveis e sai
  -h, --help                 Mostra esta ajuda

Variáveis principais (veja config.example.env):
  ANTHROPIC_API_KEY  revisões por LLM, BridgeGuard, Anticapture, Quorum
  OPENAI_API_KEY     SymGPT
  GITHUB_TOKEN       repositórios privados
EOF
}

ALVO="" REF="" SAIDA="" SO_ETAPAS="" PULAR="" CONFIG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="$2"; shift 2 ;;
    --saida) SAIDA="$2"; shift 2 ;;
    --etapas) SO_ETAPAS="$2"; shift 2 ;;
    --pular) PULAR="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --listar)
      for f in "$PIPELINE_DIR"/etapas/*.sh; do
        n=$(basename "$f" .sh); printf '  %-14s %s\n' "${n#*-}" "$(sed -n 's/^# Etapa: //p' "$f")"
      done; exit 0 ;;
    -h|--help) uso; exit 0 ;;
    -*) echo "Opção desconhecida: $1" >&2; uso; exit 1 ;;
    *) ALVO="$1"; shift ;;
  esac
done

[ -n "$ALVO" ] || { uso; exit 1; }
if [ -n "$CONFIG" ]; then
  set -a; # shellcheck disable=SC1090
  . "$CONFIG"; set +a
fi

for dep in git jq python3; do
  command -v "$dep" >/dev/null || { echo "Dependência ausente: $dep" >&2; exit 1; }
done

# ---------------------------------------------------------------- clone
NOME=$(basename "${ALVO%.git}")
SAIDA="${SAIDA:-$PWD/resultados/$NOME-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$SAIDA"
SAIDA="$(cd "$SAIDA" && pwd)"
export OUT_DIR="$SAIDA/etapas" INV_DIR="$SAIDA/inventario"
mkdir -p "$OUT_DIR" "$INV_DIR"

if [ -d "$ALVO" ]; then
  REPO_DIR="$(cd "$ALVO" && pwd)"
else
  [[ "$ALVO" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?/?$ ]] \
    || { echo "URL inválida (esperado https://github.com/org/repo): $ALVO" >&2; exit 1; }
  REPO_DIR="$SAIDA/repo"
  git_auth=()
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    git_auth=(-c "http.https://github.com/.extraheader=Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 -w0)")
  fi
  echo "Clonando $ALVO ..."
  if [ -n "$REF" ]; then
    git "${git_auth[@]}" clone --quiet --filter=blob:none "$ALVO" "$REPO_DIR" \
      && git -C "$REPO_DIR" checkout --quiet "$REF" \
      && git "${git_auth[@]}" -C "$REPO_DIR" submodule update --quiet --init --recursive --depth 1
  else
    git "${git_auth[@]}" clone --quiet --depth 1 --recurse-submodules --shallow-submodules "$ALVO" "$REPO_DIR"
  fi || { echo "Falha ao clonar $ALVO" >&2; exit 1; }
fi
export REPO_DIR
COMMIT=$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || echo "n/d")

# ---------------------------------------------------------------- inventário
echo "Inventariando $REPO_DIR ..."
EXCL=(--exclude-dir=.git --exclude-dir=node_modules --exclude-dir=lib --exclude-dir=out
      --exclude-dir=cache --exclude-dir=artifacts --exclude-dir=dist --exclude-dir=build
      --exclude-dir=vendor --exclude-dir=target)
CODIGO=(--include='*.sol' --include='*.vy' --include='*.circom' --include='*.nr'
        --include='*.ts' --include='*.js' --include='*.rs' --include='*.go' --include='*.py')

lista() { # lista <nome> <regex> [includes...]
  local nome="$1" re="$2"; shift 2
  (cd "$REPO_DIR" && grep -rlIE "${EXCL[@]}" "$@" -e "$re" . 2>/dev/null | sed 's|^\./||' | sort) > "$INV_DIR/$nome.txt"
}

(cd "$REPO_DIR" && find . \( -name .git -o -name node_modules -o -name lib -o -name out -o -name cache \
    -o -name artifacts -o -name test -o -name tests -o -name script -o -name mocks -o -name mock \) -prune \
    -o -type f -name '*.sol' ! -name '*.t.sol' ! -name '*.s.sol' -print | sed 's|^\./||' | sort) > "$INV_DIR/solidity.txt"
(cd "$REPO_DIR" && find . \( -name .git -o -name node_modules \) -prune -o -type f -name '*.circom' -print \
    | sed 's|^\./||' | sort) > "$INV_DIR/circom.txt"
: > "$INV_DIR/circom_main.txt"
while read -r f; do grep -qE '^\s*component\s+main' "$REPO_DIR/$f" && echo "$f"; done \
  < "$INV_DIR/circom.txt" > "$INV_DIR/circom_main.txt"
# Diretórios raiz dos contratos (src/, contracts/...) para não varrer dependências.
awk -F/ '{print (NF>1 ? $1 : ".")}' "$INV_DIR/solidity.txt" | sort -u > "$INV_DIR/sol_raizes.txt"

lista erc '(is|,)\s*(I?ERC(20|721|1155|4626|777|2981|165))\b' --include='*.sol'
# Mantém só contratos de produção (sem testes/mocks).
grep -xF -f "$INV_DIR/solidity.txt" "$INV_DIR/erc.txt" > "$INV_DIR/erc.tmp" 2>/dev/null
mv "$INV_DIR/erc.tmp" "$INV_DIR/erc.txt"
lista bridge '[Bb]ridge|LayerZero|lzReceive|Wormhole|CCIP|ccipReceive|Axelar|Hyperlane|Hyperliquid|crossChain|relayMessage|xcall' "${CODIGO[@]}"
lista governanca 'Governor|TimelockController|ERC20Votes|IVotes|castVote|proposalThreshold|quorumNumerator' --include='*.sol'
lista privacidade 'nullifier|Nullifier|shielded|[Mm]ixer|IncrementalMerkle|commitments\[|noteCommitment' "${CODIGO[@]}"
lista zktls 'tlsn|TLSNotary|tlsnotary|@reclaimprotocol|reclaim-sdk|attestor-core|ReclaimVerifier|[Zz]kTLS|zkFetch' "${CODIGO[@]}" --include='*.json' --include='*.toml'
(cd "$REPO_DIR" && grep -rhoIE "${EXCL[@]}" "${CODIGO[@]}" --include='*.json' --include='*.env*' --include='*.toml' \
    'https?://[A-Za-z0-9._~:/?#@!$&+,;=%-]*(rpc|infura|alchemy|quiknode|ankr|graphql|subgraph)[A-Za-z0-9._~:/?#@!$&+,;=%-]*' . 2>/dev/null \
    | sort -u) > "$INV_DIR/urls_rpc.txt"

# Escopo para o relatório profissional: sha256, linhas e caminho de cada arquivo auditável.
(cd "$REPO_DIR" && cat "$INV_DIR/solidity.txt" "$INV_DIR/circom.txt" | while read -r f; do
  [ -f "$f" ] && printf '%s\t%s\t%s\n' "$(sha256sum "$f" | cut -c1-16)" "$(wc -l < "$f" | tr -d ' ')" "$f"
done) > "$INV_DIR/escopo.tsv"

TIPO="nenhum"
[ -f "$REPO_DIR/foundry.toml" ] && TIPO="foundry"
ls "$REPO_DIR"/hardhat.config.* >/dev/null 2>&1 && TIPO="${TIPO/nenhum/hardhat}"
echo "$TIPO" > "$INV_DIR/projeto.txt"

contar() { wc -l < "$INV_DIR/$1.txt" | tr -d ' '; }
jq -n --arg alvo "$ALVO" --arg commit "$COMMIT" --arg projeto "$TIPO" \
  --argjson solidity "$(contar solidity)" --argjson circom "$(contar circom)" \
  --argjson circom_main "$(contar circom_main)" --argjson erc "$(contar erc)" \
  --argjson bridge "$(contar bridge)" --argjson governanca "$(contar governanca)" \
  --argjson privacidade "$(contar privacidade)" --argjson zktls "$(contar zktls)" \
  --argjson urls_rpc "$(contar urls_rpc)" --arg data "$(date -u +%FT%TZ)" \
  '{alvo:$alvo, commit:$commit, data:$data, projeto:$projeto,
    arquivos:{solidity:$solidity, circom:$circom, circom_main:$circom_main, erc:$erc,
              bridge:$bridge, governanca:$governanca, privacidade:$privacidade,
              zktls:$zktls, urls_rpc:$urls_rpc}}' > "$INV_DIR/resumo.json"
jq -r '.arquivos | to_entries[] | "  \(.key): \(.value)"' "$INV_DIR/resumo.json"

# ---------------------------------------------------------------- etapas
na_lista() { [[ ",$2," == *",$1,"* ]]; }
for f in "$PIPELINE_DIR"/etapas/*.sh; do
  n=$(basename "$f" .sh); n="${n#*-}"
  [ -n "$SO_ETAPAS" ] && ! na_lista "$n" "$SO_ETAPAS" && continue
  [ -n "$PULAR" ] && na_lista "$n" "$PULAR" && continue
  bash "$f" || echo "[$n] terminou com código $? (ignorado)" >&2
done

# ---------------------------------------------------------------- relatório
python3 "$PIPELINE_DIR/relatorio.py" "$SAIDA"
python3 "$PIPELINE_DIR/relatorio_profissional.py" "$SAIDA" ${TRIAGEM:+--triagem "$TRIAGEM"}
echo
echo "Relatório técnico:       $SAIDA/RELATORIO.md"
echo "Rascunho do relatório:   $SAIDA/AUDITORIA.md (complete a triagem e gere de novo)"
