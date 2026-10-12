# Plataforma Multi-Agente

Times de agentes autônomos com interface web. Stack:

| Camada | Tecnologia |
|---|---|
| LLM | **OLMo 3 32B** (`olmo-3.1:32b-instruct`) |
| Agentes | **[Conway Automaton](https://github.com/Conway-Research/automaton)** |
| Orquestração | **LangGraph** + **n8n** |
| Hosting | **[Flux](https://runonflux.com/)**: OLMo numa GPU da FluxEdge, plataforma na FluxCloud |

📖 Guia completo: [`guia.mdx`](guia.mdx)

## Início rápido

```bash
cp .env.example .env          # defina RUNNER_TOKEN e ADMIN_PASSWORD
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build   # OLMo na mesma máquina (GPU)
```

Painel em http://localhost:8080 · API em http://localhost:8080/docs

## Pastas

| Pasta | O que é |
|---|---|
| `control-plane/` | FastAPI: painel, login, API REST, orquestrador LangGraph, carteiras e emulação local da Conway Cloud |
| `automaton-runner/` | Conway Automaton (commit fixado + patch), um processo por agente, e as carteiras EVM |
| `n8n/` | Workflow de exemplo: webhook → executa time → responde |
| `flux/` | Spec da FluxCloud, OLMo na FluxEdge (`olmo-gpu/`) e script para publicar as imagens |
