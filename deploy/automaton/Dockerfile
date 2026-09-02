# Conway Automaton (https://github.com/Conway-Research/automaton)
#
# Este Dockerfile clona e builda o Automaton a partir do upstream. Fixe
# AUTOMATON_REF em um commit/tag específico antes de ir para produção —
# não rode `main` sem revisar o que mudou desde a última auditoria.

FROM node:20-slim AS build

ARG AUTOMATON_REPO=https://github.com/Conway-Research/automaton.git
ARG AUTOMATON_REF=main

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN git clone --depth 1 --branch ${AUTOMATON_REF} ${AUTOMATON_REPO} .
RUN npm install && npm run build

FROM node:20-slim
WORKDIR /app
COPY --from=build /app /app

# Genesis prompt / constituição customizada do Módulo 1 – Vendas
# (substitui a identidade padrão do projeto pela mandate abaixo).
COPY genesis-prompt.md /app/config/genesis-prompt.md

EXPOSE 3000
CMD ["node", "dist/index.js", "--run"]
