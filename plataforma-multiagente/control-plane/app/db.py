"""Persistência em SQLite: agentes, mensagens (relay), uso de LLM e execuções de times."""

import json
import os
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT '',
  genesis_prompt TEXT NOT NULL,
  model TEXT NOT NULL,
  address TEXT,
  created_at TEXT NOT NULL
);

-- direction: 'to_agent' (plataforma/outro agente -> agente) ou 'from_agent'
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  direction TEXT NOT NULL,
  from_addr TEXT NOT NULL,
  to_addr TEXT NOT NULL,
  content TEXT NOT NULL,
  reply_to TEXT,
  run_id TEXT,
  step_id TEXT,
  created_at TEXT NOT NULL,
  delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_pending ON messages(agent_id, direction, delivered_at);

CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT,
  requested_model TEXT,
  model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wallet_txs (
  hash TEXT PRIMARY KEY,
  wallet_id TEXT NOT NULL,
  asset TEXT NOT NULL,
  amount TEXT NOT NULL,
  from_addr TEXT NOT NULL,
  to_addr TEXT NOT NULL,
  network TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_by TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  task TEXT NOT NULL,
  agent_ids TEXT NOT NULL,
  executor TEXT NOT NULL,
  status TEXT NOT NULL,
  plan TEXT,
  results TEXT,
  final TEXT,
  error TEXT,
  events TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
        _conn = sqlite3.connect(settings.db_path, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        return conn().execute(sql, params).rowcount


def creator_address() -> str:
    """Endereço EVM que representa a plataforma (o "criador" de todos os agentes)."""
    rows = query("SELECT value FROM kv WHERE key = 'creator_address'")
    if rows:
        return rows[0]["value"]
    address = "0x" + secrets.token_hex(20)
    execute("INSERT OR IGNORE INTO kv (key, value) VALUES ('creator_address', ?)", (address,))
    return query("SELECT value FROM kv WHERE key = 'creator_address'")[0]["value"]


# ─── Agentes ─────────────────────────────────────────────────────────


def list_agents() -> list[dict]:
    return query("SELECT * FROM agents ORDER BY created_at")


def get_agent(agent_id: str) -> dict | None:
    rows = query("SELECT * FROM agents WHERE id = ?", (agent_id,))
    return rows[0] if rows else None


def agent_by_address(address: str) -> dict | None:
    rows = query("SELECT * FROM agents WHERE lower(address) = lower(?)", (address,))
    return rows[0] if rows else None


# ─── Mensagens ───────────────────────────────────────────────────────


def add_message(
    agent_id: str,
    direction: str,
    from_addr: str,
    to_addr: str,
    content: str,
    reply_to: str | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
) -> str:
    msg_id = new_id()
    execute(
        "INSERT INTO messages (id, agent_id, direction, from_addr, to_addr, content, reply_to, run_id, step_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, agent_id, direction, from_addr, to_addr, content, reply_to, run_id, step_id, now()),
    )
    return msg_id


def take_pending(agent_id: str, limit: int = 20) -> list[dict]:
    """Retorna e marca como entregues as mensagens que aguardam o agente."""
    with _lock:
        c = conn()
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM messages WHERE agent_id = ? AND direction = 'to_agent' AND delivered_at IS NULL"
                " ORDER BY created_at LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        ]
        stamp = now()
        for r in rows:
            c.execute("UPDATE messages SET delivered_at = ? WHERE id = ?", (stamp, r["id"]))
        return rows


def count_pending(agent_id: str) -> int:
    rows = query(
        "SELECT COUNT(*) AS n FROM messages WHERE agent_id = ? AND direction = 'to_agent' AND delivered_at IS NULL",
        (agent_id,),
    )
    return rows[0]["n"]


def conversation(agent_id: str, limit: int = 200) -> list[dict]:
    return query(
        "SELECT * FROM (SELECT * FROM messages WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?) ORDER BY created_at",
        (agent_id, limit),
    )


def oldest_open_step(agent_id: str) -> dict | None:
    """Mensagem de tarefa (de um run) mais antiga deste agente que ainda não teve resposta."""
    rows = query(
        "SELECT m.* FROM messages m JOIN runs ON runs.id = m.run_id"
        " WHERE m.agent_id = ? AND m.direction = 'to_agent' AND m.step_id IS NOT NULL"
        " AND runs.status IN ('queued', 'running')"
        " AND NOT EXISTS (SELECT 1 FROM messages r WHERE r.direction = 'from_agent' AND r.step_id = m.step_id)"
        " ORDER BY m.created_at LIMIT 1",
        (agent_id,),
    )
    return rows[0] if rows else None


def step_by_tag(agent_id: str, tag: str) -> dict | None:
    """Tarefa deste agente cuja etiqueta (#prefixo do step_id) aparece na resposta."""
    rows = query(
        "SELECT * FROM messages WHERE agent_id = ? AND direction = 'to_agent' AND step_id LIKE ? ORDER BY created_at LIMIT 1",
        (agent_id, f"{tag}%"),
    )
    return rows[0] if rows else None


def message(msg_id: str) -> dict | None:
    rows = query("SELECT * FROM messages WHERE id = ?", (msg_id,))
    return rows[0] if rows else None


def step_replies(run_id: str) -> dict[str, str]:
    rows = query(
        "SELECT step_id, content FROM messages WHERE run_id = ? AND direction = 'from_agent' ORDER BY created_at",
        (run_id,),
    )
    replies: dict[str, str] = {}
    for r in rows:
        replies.setdefault(r["step_id"], r["content"])
    return replies


# ─── Uso de LLM ──────────────────────────────────────────────────────


def record_usage(agent_id: str | None, requested: str | None, model: str, usage: dict, latency_ms: int) -> None:
    execute(
        "INSERT INTO llm_usage (agent_id, requested_model, model, prompt_tokens, completion_tokens, latency_ms, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            agent_id,
            requested,
            model,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            latency_ms,
            now(),
        ),
    )


def usage_by_agent() -> dict[str, dict]:
    rows = query(
        "SELECT agent_id, COUNT(*) AS calls, SUM(prompt_tokens) AS prompt_tokens,"
        " SUM(completion_tokens) AS completion_tokens, MAX(created_at) AS last_call"
        " FROM llm_usage GROUP BY agent_id"
    )
    return {r["agent_id"] or "_platform": r for r in rows}


# ─── Runs (times) ────────────────────────────────────────────────────


def create_run(task: str, agent_ids: list[str], executor: str) -> str:
    run_id = new_id()
    stamp = now()
    execute(
        "INSERT INTO runs (id, task, agent_ids, executor, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, ?)",
        (run_id, task, json.dumps(agent_ids), executor, stamp, stamp),
    )
    return run_id


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = []
    values = []
    for key, value in fields.items():
        cols.append(f"{key} = ?")
        values.append(json.dumps(value, ensure_ascii=False) if key in ("plan", "results") else value)
    cols.append("updated_at = ?")
    values.append(now())
    execute(f"UPDATE runs SET {', '.join(cols)} WHERE id = ?", (*values, run_id))


def add_run_event(run_id: str, text: str) -> None:
    with _lock:
        c = conn()
        row = c.execute("SELECT events FROM runs WHERE id = ?", (run_id,)).fetchone()
        events = json.loads(row["events"]) if row else []
        events.append({"at": now(), "text": text})
        c.execute("UPDATE runs SET events = ?, updated_at = ? WHERE id = ?", (json.dumps(events, ensure_ascii=False), now(), run_id))


def get_run(run_id: str) -> dict | None:
    rows = query("SELECT * FROM runs WHERE id = ?", (run_id,))
    return _decode_run(rows[0]) if rows else None


def list_runs(limit: int = 50) -> list[dict]:
    return [_decode_run(r) for r in query("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))]


def _decode_run(row: dict) -> dict:
    for key in ("agent_ids", "plan", "results", "events"):
        if row.get(key):
            row[key] = json.loads(row[key])
    return row
