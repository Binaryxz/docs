# Imagem com todas as ferramentas do pipeline de auditoria Web3.
#   docker build -t auditoria-web3 auditoria-web3/
#   docker run --rm --env-file config.env -v "$PWD/resultados:/resultados" \
#     auditoria-web3 https://github.com/org/repo --saida /resultados/repo
FROM ubuntu:24.04

ARG GO_VERSION=1.24.4
ARG CIRCOM_VERSION=v2.2.2
ARG MEDUSA_VERSION=latest
ARG BRIDGEGUARD_VERSION=0.1.1
ARG NODE_VERSION=22.11.0

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/venv/bin:/usr/local/go/bin:/root/go/bin:/root/.foundry/bin:/opt/Picus:$PATH \
    PICUS_SOLVER=z3 \
    SOLIDITYGUARD_HOME=/opt/SolidityGuard \
    SYMGPT_HOME=/opt/symgpt

RUN apt-get update && apt-get install -y --no-install-recommends \
      git jq curl ca-certificates build-essential unzip \
      python3 python3-venv python3-dev xz-utils \
      racket z3 \
    && rm -rf /var/lib/apt/lists/*

# Node 22 oficial: projetos atuais exigem Node >= 20, e o do Ubuntu 24.04 é o 18.
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
      | tar -xJ -C /usr/local --strip-components=1 \
    && node --version && npm --version

# Go + Medusa (fuzzer)
RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" | tar -C /usr/local -xz \
    && go install "github.com/crytic/medusa@${MEDUSA_VERSION}"

# Foundry (compilação de projetos forge para o Medusa)
RUN curl -fsSL https://foundry.paradigm.xyz | bash && /root/.foundry/bin/foundryup

# Circom (usado pelo Picus e pelo --inspect)
RUN curl -fsSL -o /usr/local/bin/circom \
      "https://github.com/iden3/circom/releases/download/${CIRCOM_VERSION}/circom-linux-amd64" \
    && chmod +x /usr/local/bin/circom

# Ferramentas Python: MIESC (Slither, Mythril...), Quorum, Nodeprobe
RUN python3 -m venv /opt/venv \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "miesc[full]" slither-analyzer crytic-compile solc-select \
         certora-quorum nodeprobe \
    && solc-select install latest && solc-select use latest

# Ferramentas opcionais: se a instalação de uma falhar, o build continua e a
# etapa correspondente é pulada (aparece como "não instalado" no relatório).

# Picus (Veridise)
RUN (git clone --depth 1 https://github.com/Veridise/Picus.git /opt/Picus \
      && cd /opt/Picus && raco pkg install --auto --batch --no-docs) \
    || { echo "AVISO: Picus não instalado"; rm -rf /opt/Picus; }

# SolidityGuard
RUN (git clone --depth 1 https://github.com/alt-research2/SolidityGuard.git /opt/SolidityGuard \
      && pip install --no-cache-dir -e /opt/SolidityGuard/apps/cli) \
    || { echo "AVISO: SolidityGuard não instalado"; rm -rf /opt/SolidityGuard; }

# SymGPT
RUN (git clone --depth 1 https://github.com/symbolic-gpt/symgpt.git /opt/symgpt \
      && pip install --no-cache-dir -e /opt/symgpt) \
    || { echo "AVISO: SymGPT não instalado"; rm -rf /opt/symgpt; }

# Claude Code (revisões por LLM) + BridgeGuard MCP
RUN npm install -g @anthropic-ai/claude-code "bridgeguard-mcp@${BRIDGEGUARD_VERSION}" \
    && npm cache clean --force

COPY . /opt/auditoria-web3
RUN chmod +x /opt/auditoria-web3/auditar.sh /opt/auditoria-web3/etapas/*.sh
WORKDIR /resultados
ENTRYPOINT ["/opt/auditoria-web3/auditar.sh"]
