"""Cliente do LLM (OLMo 3 32B), servido pelo Ollama com API compatível com OpenAI.

O mesmo cliente atende:
  - o proxy que os agentes Automaton usam (`forward`), e
  - o orquestrador LangGraph da plataforma (`chat`).
"""

import time

import httpx

from . import db
from .config import settings

# Parâmetros específicos de provedores pagos que servidores locais não entendem.
_DROP_KEYS = {"reasoning_effort", "store", "metadata", "service_tier", "parallel_tool_calls"}

_client = httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout_s, connect=10))


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}


def normalize_body(body: dict) -> dict:
    """Força o modelo configurado e remove campos que o Ollama rejeita.

    O Automaton escolhe modelos pelo nome (gpt-5.2, gpt-5-mini, ...). Aqui
    qualquer nome é reescrito para o OLMo configurado em LLM_MODEL.
    """
    out = {k: v for k, v in body.items() if k not in _DROP_KEYS}
    out["model"] = settings.llm_model
    out["stream"] = False
    if "max_completion_tokens" in out:
        out.setdefault("max_tokens", out.pop("max_completion_tokens"))
    if not out.get("tools"):
        out.pop("tools", None)
        out.pop("tool_choice", None)
    return out


async def forward(body: dict, agent_id: str | None) -> tuple[int, dict]:
    requested = body.get("model")
    payload = normalize_body(body)
    started = time.monotonic()
    try:
        resp = await _client.post(f"{settings.llm_base_url}/chat/completions", json=payload, headers=_headers())
    except httpx.HTTPError as err:
        return 502, {"error": {"message": f"LLM indisponível em {settings.llm_base_url}: {err}"}}
    latency = int((time.monotonic() - started) * 1000)
    try:
        data = resp.json()
    except ValueError:
        return 502, {"error": {"message": f"Resposta inválida do LLM ({resp.status_code}): {resp.text[:500]}"}}
    if resp.status_code < 400:
        db.record_usage(agent_id, requested, settings.llm_model, data.get("usage") or {}, latency)
    return resp.status_code, data


async def chat(messages: list[dict], *, temperature: float = 0.2, max_tokens: int = 2048, json_mode: bool = False) -> str:
    body: dict = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    status, data = await forward(body, None)
    if status >= 400:
        raise RuntimeError(f"LLM retornou {status}: {data}")
    return (data["choices"][0]["message"].get("content") or "").strip()


async def health() -> dict:
    try:
        resp = await _client.get(f"{settings.llm_base_url}/models", headers=_headers(), timeout=5)
        models = [m.get("id") for m in resp.json().get("data", [])] if resp.status_code == 200 else []
        return {"ok": resp.status_code == 200, "models": models, "configured": settings.llm_model}
    except (httpx.HTTPError, ValueError) as err:
        return {"ok": False, "error": str(err), "configured": settings.llm_model}
