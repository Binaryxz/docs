"""Emulação local da Conway Cloud para o Conway Automaton.

O Automaton foi feito para falar com api.conway.tech (créditos, registro,
inferência) e social.conway.tech (relay de mensagens entre agentes). O runner
aponta todas essas URLs para `https://localhost:8443/a/<agent_id>`, que chega
aqui como `/conway/a/<agent_id>/...`. Com isso cada agente:

  - recebe créditos "infinitos" (não há cobrança: o LLM é o seu OLMo),
  - faz inferência no OLMo via `/v1/chat/completions` ou pela rota Ollama,
  - troca mensagens com a plataforma e com outros agentes pelo relay local.

Replicação (sandboxes filhas), domínios e pagamentos x402 não são emulados.
"""

import re
import secrets

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from . import db, llm
from .config import settings

router = APIRouter(prefix="/conway/a/{agent_id}")

UNLIMITED_CREDITS_CENTS = 1_000_000_00  # US$ 1M: mantém o agente no tier "high"
TAG_RE = re.compile(r"#([0-9a-f]{8})\b")


def _agent(agent_id: str, token: str | None) -> dict:
    if settings.runner_token and token != settings.runner_token:
        raise HTTPException(401, "runner token inválido")
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"agente {agent_id} não registrado na plataforma")
    return agent


# ─── Créditos / registro ─────────────────────────────────────────────


@router.get("/v1/credits/balance")
async def credits_balance(agent_id: str, x_runner_token: str | None = Header(None)):
    _agent(agent_id, x_runner_token)
    return {"balance_cents": UNLIMITED_CREDITS_CENTS, "credits_cents": UNLIMITED_CREDITS_CENTS}


@router.get("/v1/credits/pricing")
async def credits_pricing(agent_id: str, x_runner_token: str | None = Header(None)):
    _agent(agent_id, x_runner_token)
    return {"tiers": []}


@router.post("/v1/automatons/register")
async def register(agent_id: str, request: Request, x_runner_token: str | None = Header(None)):
    _agent(agent_id, x_runner_token)
    body = await request.json()
    if body.get("automatonAddress"):
        db.execute("UPDATE agents SET address = ? WHERE id = ?", (body["automatonAddress"], agent_id))
    return {"ok": True, "automaton_id": agent_id}


# ─── Inferência (OpenAI-compatível + descoberta estilo Ollama) ───────


@router.get("/v1/models")
async def models(agent_id: str, x_runner_token: str | None = Header(None)):
    _agent(agent_id, x_runner_token)
    return {"object": "list", "data": [{"id": settings.llm_model, "object": "model", "owned_by": "local"}]}


@router.get("/api/tags")
async def ollama_tags(agent_id: str, x_runner_token: str | None = Header(None)):
    _agent(agent_id, x_runner_token)
    return {"models": [{"name": settings.llm_model, "model": settings.llm_model, "details": {}}]}


@router.post("/v1/chat/completions")
async def chat_completions(agent_id: str, request: Request, x_runner_token: str | None = Header(None)):
    _agent(agent_id, x_runner_token)
    status, data = await llm.forward(await request.json(), agent_id)
    return JSONResponse(data, status_code=status)


# ─── Relay social ────────────────────────────────────────────────────


@router.post("/v1/messages")
async def relay_send(agent_id: str, request: Request, x_runner_token: str | None = Header(None)):
    """Mensagem enviada pelo agente (tool `send_message`)."""
    sender = _agent(agent_id, x_runner_token)
    body = await request.json()
    to_addr = (body.get("to") or "").lower()
    content = body.get("content") or ""
    reply_to = body.get("reply_to")

    # Amarra a resposta à tarefa de um run, nesta ordem: etiqueta #xxxxxxxx no
    # texto, reply_to (se o agente usou), tarefa mais antiga ainda sem resposta.
    step = None
    if to_addr == db.creator_address().lower():
        for tag in TAG_RE.findall(content):
            step = db.step_by_tag(agent_id, tag)
            if step:
                break
        if not step and reply_to:
            candidate = db.message(reply_to)
            if candidate and candidate["agent_id"] == agent_id and candidate["step_id"]:
                step = candidate
        if not step:
            step = db.oldest_open_step(agent_id)

    msg_id = db.add_message(
        agent_id,
        "from_agent",
        sender.get("address") or body.get("from", ""),
        to_addr,
        content,
        reply_to=reply_to,
        run_id=step["run_id"] if step else None,
        step_id=step["step_id"] if step else None,
    )

    # Agente → agente: entrega na caixa do destinatário.
    target = db.agent_by_address(to_addr)
    if target and target["id"] != agent_id:
        db.add_message(target["id"], "to_agent", sender.get("address") or "", to_addr, content, reply_to=reply_to)

    return {"id": msg_id}


@router.post("/v1/messages/poll")
async def relay_poll(agent_id: str, x_runner_token: str | None = Header(None)):
    agent = _agent(agent_id, x_runner_token)
    messages = [
        {
            "id": m["id"],
            "from": m["from_addr"],
            "to": agent.get("address") or m["to_addr"],
            "content": m["content"],
            "signedAt": m["created_at"],
            "createdAt": m["created_at"],
            "replyTo": m["reply_to"],
            "nonce": secrets.token_hex(8),
        }
        for m in db.take_pending(agent_id)
    ]
    return {"messages": messages, "next_cursor": messages[-1]["id"] if messages else None}


@router.get("/v1/messages/count")
async def relay_count(agent_id: str, x_runner_token: str | None = Header(None)):
    _agent(agent_id, x_runner_token)
    n = db.count_pending(agent_id)
    return {"count": n, "unread": n}


# ─── Demais rotas da Conway ──────────────────────────────────────────


@router.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def not_emulated(agent_id: str, rest: str, x_runner_token: str | None = Header(None)):
    _agent(agent_id, x_runner_token)
    return JSONResponse(
        {"error": f"/{rest} não é emulado no modo local (sandboxes, domínios e pagamentos exigem a Conway Cloud)"},
        status_code=501,
    )
