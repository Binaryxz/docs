"""Control plane da plataforma multi-agente: API REST, UI web e emulação Conway."""

import asyncio
import re
import secrets
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import conway, db, llm, runner, teams
from .config import settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Runs rodam em tarefas asyncio deste processo; os que estavam em andamento
    # num processo anterior não têm mais quem os conclua.
    for run in db.list_runs(limit=1000):
        if run["status"] in ("queued", "running"):
            db.update_run(run["id"], status="error", error="interrompido por reinício do control plane")
    yield


app = FastAPI(title="Plataforma Multi-Agente", version="0.1.0", lifespan=lifespan)
app.include_router(conway.router)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_background: set[asyncio.Task] = set()

TEMPLATES = [
    {
        "name": "Pesquisador",
        "role": "pesquisa e levantamento de fatos",
        "genesis_prompt": "Você levanta informações relevantes, cita fontes quando tiver acesso a elas e separa fatos de suposições.",
    },
    {
        "name": "Redator",
        "role": "redação e revisão de textos em português",
        "genesis_prompt": "Você transforma insumos em textos claros, objetivos e bem estruturados, no tom pedido.",
    },
    {
        "name": "Engenheiro",
        "role": "programação, scripts e automação",
        "genesis_prompt": "Você escreve, executa e testa código no seu ambiente Linux para resolver tarefas técnicas.",
    },
    {
        "name": "Analista",
        "role": "análise crítica, riscos e próximos passos",
        "genesis_prompt": "Você avalia propostas, aponta riscos e lacunas e recomenda próximos passos concretos.",
    },
]


def require_admin(authorization: str | None = Header(None)) -> None:
    if settings.admin_token and authorization != f"Bearer {settings.admin_token}":
        raise HTTPException(401, "token de administrador inválido")


def _slug(name: str) -> str:
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:40] or "agente"
    return f"{base}-{secrets.token_hex(3)}"


# ─── Modelos de entrada ──────────────────────────────────────────────


class AgentIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    role: str = Field(default="", max_length=200)
    genesis_prompt: str = Field(min_length=10, max_length=8000)
    start: bool = True


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class RunIn(BaseModel):
    task: str = Field(min_length=5, max_length=8000)
    agent_ids: list[str] = Field(min_length=1)
    executor: str = Field(default="automaton", pattern="^(automaton|llm)$")
    wait: bool = False  # bloqueia até terminar (útil no n8n)


# ─── UI ──────────────────────────────────────────────────────────────


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True}


# ─── API ─────────────────────────────────────────────────────────────

api = Depends(require_admin)


@app.get("/api/status", dependencies=[api])
async def status():
    llm_health, runner_ok = await asyncio.gather(llm.health(), runner.health())
    return {
        "llm": {**llm_health, "base_url": settings.llm_base_url},
        "runner": {"ok": runner_ok, "url": settings.runner_url},
        "creator_address": db.creator_address(),
    }


@app.get("/api/templates", dependencies=[api])
async def templates():
    return TEMPLATES


@app.get("/api/agents", dependencies=[api])
async def list_agents():
    statuses = await runner.statuses()
    usage = db.usage_by_agent()
    return [
        {**a, "runtime": statuses.get(a["id"]), "usage": usage.get(a["id"]), "pending": db.count_pending(a["id"])}
        for a in db.list_agents()
    ]


@app.post("/api/agents", dependencies=[api], status_code=201)
async def create_agent(body: AgentIn):
    agent_id = _slug(body.name)
    db.execute(
        "INSERT INTO agents (id, name, role, genesis_prompt, model, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (agent_id, body.name, body.role, body.genesis_prompt, settings.llm_model, db.now()),
    )
    try:
        created = await runner.create(
            {
                "id": agent_id,
                "name": body.name,
                "role": body.role,
                "genesisPrompt": body.genesis_prompt,
                "creatorAddress": db.creator_address(),
                "model": settings.llm_model,
                "start": body.start,
            }
        )
    except RuntimeError as err:
        # Sem runner o agente continua utilizável no executor "llm".
        return {**db.get_agent(agent_id), "warning": str(err)}
    db.execute("UPDATE agents SET address = ? WHERE id = ?", (created.get("address"), agent_id))
    return db.get_agent(agent_id)


def _get_agent_or_404(agent_id: str) -> dict:
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "agente não encontrado")
    return agent


@app.post("/api/agents/{agent_id}/start", dependencies=[api])
async def start_agent(agent_id: str):
    _get_agent_or_404(agent_id)
    try:
        return await runner.start(agent_id)
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err


@app.post("/api/agents/{agent_id}/stop", dependencies=[api])
async def stop_agent(agent_id: str):
    _get_agent_or_404(agent_id)
    try:
        return await runner.stop(agent_id)
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err


@app.delete("/api/agents/{agent_id}", dependencies=[api])
async def delete_agent(agent_id: str):
    _get_agent_or_404(agent_id)
    try:
        await runner.delete(agent_id)
    except RuntimeError:
        pass
    db.execute("DELETE FROM messages WHERE agent_id = ?", (agent_id,))
    db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    return {"deleted": agent_id}


@app.get("/api/agents/{agent_id}/messages", dependencies=[api])
async def agent_messages(agent_id: str):
    _get_agent_or_404(agent_id)
    return db.conversation(agent_id)


@app.post("/api/agents/{agent_id}/messages", dependencies=[api], status_code=201)
async def send_to_agent(agent_id: str, body: MessageIn):
    agent = _get_agent_or_404(agent_id)
    creator = db.creator_address()
    content = f"{body.content}\n\n(Responda com a ferramenta send_message para {creator}.)"
    msg_id = db.add_message(agent_id, "to_agent", creator, agent.get("address") or "", content)
    return db.message(msg_id)


@app.get("/api/agents/{agent_id}/logs", dependencies=[api], response_class=PlainTextResponse)
async def agent_logs(agent_id: str, lines: int = 200):
    _get_agent_or_404(agent_id)
    try:
        return await runner.logs(agent_id, lines)
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err


@app.get("/api/runs", dependencies=[api])
async def list_runs():
    return db.list_runs()


@app.get("/api/runs/{run_id}", dependencies=[api])
async def get_run(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "run não encontrado")
    return run


@app.post("/api/runs", dependencies=[api], status_code=201)
async def create_run(body: RunIn):
    for agent_id in body.agent_ids:
        _get_agent_or_404(agent_id)
    run_id = db.create_run(body.task, body.agent_ids, body.executor)
    task = asyncio.create_task(teams.execute_run(run_id))
    _background.add(task)
    task.add_done_callback(_background.discard)
    if body.wait:
        await task
    return db.get_run(run_id)
