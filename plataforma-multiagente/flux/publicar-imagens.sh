#!/bin/sh
# Builda e publica as imagens usadas pela spec da Flux (amd64, exigido pela Flux).
#   ./flux/publicar-imagens.sh seu_usuario_dockerhub 0.1.0
set -eu
USER="${1:?uso: $0 <usuario-dockerhub> [versao]}"
VERSION="${2:-0.1.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

docker buildx build --platform linux/amd64 --push \
  -t "docker.io/$USER/multiagente-control-plane:$VERSION" "$ROOT/control-plane"
docker buildx build --platform linux/amd64 --push \
  -t "docker.io/$USER/multiagente-automaton-runner:$VERSION" "$ROOT/automaton-runner"

echo "Imagens publicadas. Atualize repotag em flux/app-spec.json para a versão $VERSION."
