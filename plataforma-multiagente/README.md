# Plataforma Multi-Agente

Crie e orquestre times de agentes autônomos com **OLMo 3.1 32B Instruct**, **Conway Automaton** e **LangGraph**, com interface web. Roda local (Docker + Ollama/vLLM), opcionalmente atrás do gateway **OmniRoute**, com fluxos no **n8n**, e tem spec pronta para a **Flux**.

📖 Guia completo: [`guia.mdx`](guia.mdx)

## Início rápido

```bash
cp .env.example .env          # defina RUNNER_TOKEN
docker compose up -d --build  # adicione -f docker-compose.gpu.yml para GPU NVIDIA
docker compose logs -f ollama-pull
```

UI em http://localhost:8080 · API em http://localhost:8080/docs

## Componentes

| Pasta | O que é |
|---|---|
| `control-plane/` | FastAPI: UI, API REST, orquestrador LangGraph e emulação local da Conway Cloud |
| `automaton-runner/` | Conway Automaton (commit fixado + patch) e supervisor que roda um processo por agente |
| `n8n/` | Workflow de exemplo: webhook → executa time → responde |
| `flux/` | Spec de app da Flux e script para publicar as imagens |
