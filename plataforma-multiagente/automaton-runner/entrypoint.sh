#!/bin/sh
set -eu
TLS_DIR="${DATA_DIR:-/data}/tls"
mkdir -p "$TLS_DIR"
# Certificado autoassinado para o túnel https://localhost:8443 (o Automaton
# recusa HTTP fora de loopback para Conway API / relay / Ollama).
if [ ! -f "$TLS_DIR/cert.pem" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    -keyout "$TLS_DIR/key.pem" -out "$TLS_DIR/cert.pem" 2>/dev/null
  chmod 600 "$TLS_DIR/key.pem"
fi
# Git é usado pelo Automaton para versionar o estado de cada agente.
git config --global user.name  >/dev/null 2>&1 || git config --global user.name "automaton"
git config --global user.email >/dev/null 2>&1 || git config --global user.email "automaton@localhost"
exec node /opt/runner/server.mjs
