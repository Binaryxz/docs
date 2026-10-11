# Conway Automaton + runner multi-agente.
# O commit do Automaton é fixado para builds reprodutíveis; atualize AUTOMATON_REF
# conscientemente (o projeto muda rápido e a emulação local depende da API interna).
ARG NODE_VERSION=22

FROM node:${NODE_VERSION}-bookworm-slim AS build
ARG AUTOMATON_REPO=https://github.com/Conway-Research/automaton.git
ARG AUTOMATON_REF=d8f816881fd24b6f5e3d616e59edec387a447667
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates python3 make g++ \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/automaton
RUN git init -q . \
 && git remote add origin "$AUTOMATON_REPO" \
 && git fetch -q --depth 1 origin "$AUTOMATON_REF" \
 && git checkout -q FETCH_HEAD
# Patches locais (ver patches/): pequenos ajustes de comportamento sobre o upstream.
COPY patches /tmp/patches
RUN git apply /tmp/patches/*.patch
RUN corepack enable \
 && pnpm install --frozen-lockfile \
 && pnpm build \
 && pnpm prune --prod

FROM node:${NODE_VERSION}-bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssl ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*
# O código do Automaton fica somente-leitura para os agentes (rodam como `node`):
# a automodificação não consegue alterar o runtime compartilhado entre agentes.
COPY --from=build /opt/automaton /opt/automaton
COPY server.mjs entrypoint.sh /opt/runner/
RUN chmod 755 /opt/runner/entrypoint.sh \
 && mkdir -p /data && chown node:node /data
USER node
ENV AUTOMATON_DIR=/opt/automaton DATA_DIR=/data RUNNER_PORT=7000 TUNNEL_PORT=8443
VOLUME ["/data"]
EXPOSE 7000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS -H "Authorization: Bearer ${RUNNER_TOKEN}" http://127.0.0.1:7000/health || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/runner/entrypoint.sh"]
