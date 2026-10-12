# OLMo 3.1 32B na FluxEdge

Roda o OLMo numa GPU alugada e expõe uma API compatível com OpenAI, protegida por chave.
A plataforma (no seu computador ou na FluxCloud) usa essa API como `LLM_BASE_URL`.

## Requisitos da máquina

- GPU NVIDIA com **24 GB+** de VRAM (RTX 4090, A6000, A100, H100).
- ~30 GB de disco (imagem do Ollama + modelo de ~19 GB).

## Subir

```bash
export OLMO_API_KEY=$(openssl rand -hex 24)   # anote: vai no .env da plataforma
docker compose up -d
docker compose logs -f pull                    # espera o download do modelo terminar
curl http://localhost:8080/v1/models -H "Authorization: Bearer $OLMO_API_KEY"
```

Com um domínio apontado para a máquina, use `DOMAIN=olmo.seudominio.com docker compose up -d`:
o gateway (Caddy) emite HTTPS automaticamente na porta 443.

## Ligar na plataforma

No `.env` da plataforma:

```bash
LLM_BASE_URL=http://IP-DA-MAQUINA:8080/v1      # ou https://olmo.seudominio.com/v1
LLM_API_KEY=<o OLMO_API_KEY acima>
LLM_MODEL=olmo-3.1:32b-instruct
```

Sem domínio, a chave trafega sem criptografia: use só para testes ou restrinja o acesso ao IP da plataforma.
