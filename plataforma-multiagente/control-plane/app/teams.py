"""Orquestração multi-agente com LangGraph.

Fluxo de um "run" (uma tarefa entregue a um time de agentes):

    planejar ──▶ despachar ──▶ coletar ──▶ sintetizar

- planejar:   o OLMo divide a tarefa em subtarefas, uma por agente, pelo papel de cada um.
- despachar:  cada subtarefa vira uma mensagem na caixa de entrada do agente.
- coletar:    executor "automaton" → espera os agentes responderem via relay
              (tool `send_message`); executor "llm" → chama o OLMo direto com o
              papel do agente como system prompt (útil para testar sem Automaton).
- sintetizar: o OLMo junta as respostas numa entrega final.
"""

import asyncio
import json
import re
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from . import db, llm
from .config import settings

POLL_INTERVAL_S = 5


class RunState(TypedDict, total=False):
    run_id: str
    task: str
    executor: str
    agents: list[dict]
    plan: list[dict]  # [{step_id, agent_id, agent_name, subtask}]
    results: dict[str, str]  # step_id -> resposta
    final: str


def _event(state: RunState, text: str) -> None:
    db.add_run_event(state["run_id"], text)


def _extract_json(text: str) -> dict | list | None:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = match.group(1) if match else text
    start = min([i for i in (candidate.find("{"), candidate.find("[")) if i >= 0], default=-1)
    if start < 0:
        return None
    try:
        return json.loads(candidate[start:])
    except json.JSONDecodeError:
        try:
            return json.loads(candidate[start : max(candidate.rfind("}"), candidate.rfind("]")) + 1])
        except json.JSONDecodeError:
            return None


# ─── Nós ─────────────────────────────────────────────────────────────


async def plan_node(state: RunState) -> RunState:
    agents = state["agents"]
    roster = "\n".join(f'- id="{a["id"]}" nome="{a["name"]}" papel="{a["role"] or "generalista"}"' for a in agents)
    prompt = (
        "Você coordena um time de agentes de IA. Divida a tarefa abaixo em subtarefas "
        "independentes, no máximo uma por agente, atribuindo cada uma ao agente cujo papel "
        "combina melhor. Não atribua nada a agentes que não ajudam.\n\n"
        f"Agentes:\n{roster}\n\nTarefa:\n{state['task']}\n\n"
        'Responda SOMENTE com JSON: {"subtasks": [{"agent_id": "...", "subtask": "..."}]}'
    )
    _event(state, "Planejando subtarefas com o LLM")
    plan: list[dict] = []
    try:
        raw = await llm.chat([{"role": "user", "content": prompt}], json_mode=True)
        parsed = _extract_json(raw) or {}
        items = parsed.get("subtasks", []) if isinstance(parsed, dict) else parsed
        by_id = {a["id"]: a for a in agents}
        for item in items or []:
            agent = by_id.get(str(item.get("agent_id", "")))
            if agent and item.get("subtask") and all(p["agent_id"] != agent["id"] for p in plan):
                plan.append({"agent_id": agent["id"], "agent_name": agent["name"], "subtask": str(item["subtask"])})
    except Exception as err:  # noqa: BLE001 - qualquer falha cai no plano padrão
        _event(state, f"Planejamento falhou ({err}); usando a tarefa inteira para cada agente")

    if not plan:
        plan = [{"agent_id": a["id"], "agent_name": a["name"], "subtask": state["task"]} for a in agents]
    for item in plan:
        item["step_id"] = db.new_id()

    db.update_run(state["run_id"], status="running", plan=plan)
    _event(state, f"Plano com {len(plan)} subtarefa(s): " + ", ".join(p["agent_name"] for p in plan))
    return {"plan": plan}


async def dispatch_node(state: RunState) -> RunState:
    if state["executor"] != "automaton":
        return {}
    creator = db.creator_address()
    for item in state["plan"]:
        content = (
            f"Nova tarefa da plataforma (run {state['run_id'][:8]}).\n\n"
            f"Objetivo geral do time: {state['task']}\n\n"
            f"Sua parte: {item['subtask']}\n\n"
            f"Quando terminar, envie o resultado completo com a ferramenta send_message "
            f"para {creator}, começando a mensagem com a etiqueta #{item['step_id'][:8]}. "
            f"Envie uma única mensagem, já com o resultado final (não mande avisos de progresso)."
        )
        db.add_message(item["agent_id"], "to_agent", creator, "", content, run_id=state["run_id"], step_id=item["step_id"])
        _event(state, f"Subtarefa enviada para {item['agent_name']}")
    return {}


async def _run_with_llm(state: RunState, item: dict) -> str:
    agent = next(a for a in state["agents"] if a["id"] == item["agent_id"])
    system = f"Você é {agent['name']}. Papel: {agent['role'] or 'generalista'}.\n\n{agent['genesis_prompt']}"
    user = f"Objetivo geral do time: {state['task']}\n\nSua parte: {item['subtask']}"
    return await llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=3000)


async def collect_node(state: RunState) -> RunState:
    plan = state["plan"]

    if state["executor"] == "llm":
        answers = await asyncio.gather(*(_run_with_llm(state, item) for item in plan), return_exceptions=True)
        results = {}
        for item, answer in zip(plan, answers):
            results[item["step_id"]] = f"[erro: {answer}]" if isinstance(answer, Exception) else answer
            _event(state, f"{item['agent_name']} respondeu")
        db.update_run(state["run_id"], results=results)
        return {"results": results}

    deadline = asyncio.get_running_loop().time() + settings.run_timeout_s
    seen: set[str] = set()
    while True:
        replies = db.step_replies(state["run_id"])
        for item in plan:
            if item["step_id"] in replies and item["step_id"] not in seen:
                seen.add(item["step_id"])
                _event(state, f"{item['agent_name']} respondeu")
        db.update_run(state["run_id"], results=replies)
        if len(seen) == len(plan):
            return {"results": replies}
        if asyncio.get_running_loop().time() > deadline:
            missing = [p["agent_name"] for p in plan if p["step_id"] not in replies]
            _event(state, f"Tempo esgotado esperando: {', '.join(missing)}")
            return {"results": replies}
        await asyncio.sleep(POLL_INTERVAL_S)


async def synthesize_node(state: RunState) -> RunState:
    parts = []
    for item in state["plan"]:
        answer = state["results"].get(item["step_id"], "(sem resposta)")
        parts.append(f"### {item['agent_name']}\nSubtarefa: {item['subtask']}\n\n{answer}")
    if len(parts) == 1:
        final = state["results"].get(state["plan"][0]["step_id"], "(sem resposta)")
    else:
        _event(state, "Sintetizando respostas")
        prompt = (
            f"Tarefa original:\n{state['task']}\n\nRespostas dos agentes:\n\n"
            + "\n\n".join(parts)
            + "\n\nCombine as respostas numa entrega final única, clara e sem repetições."
        )
        try:
            final = await llm.chat([{"role": "user", "content": prompt}], max_tokens=4000)
        except Exception as err:  # noqa: BLE001
            _event(state, f"Síntese falhou ({err}); devolvendo respostas concatenadas")
            final = "\n\n".join(parts)
    return {"final": final}


def build_graph():
    graph = StateGraph(RunState)
    graph.add_node("planejar", plan_node)
    graph.add_node("despachar", dispatch_node)
    graph.add_node("coletar", collect_node)
    graph.add_node("sintetizar", synthesize_node)
    graph.add_edge(START, "planejar")
    graph.add_edge("planejar", "despachar")
    graph.add_edge("despachar", "coletar")
    graph.add_edge("coletar", "sintetizar")
    graph.add_edge("sintetizar", END)
    return graph.compile()


GRAPH = build_graph()


async def execute_run(run_id: str) -> None:
    run = db.get_run(run_id)
    agents = [a for a in (db.get_agent(i) for i in run["agent_ids"]) if a]
    state: RunState = {"run_id": run_id, "task": run["task"], "executor": run["executor"], "agents": agents}
    try:
        if not agents:
            raise RuntimeError("nenhum agente válido no time")
        health = await llm.health()
        if not health.get("ok"):
            raise RuntimeError(health.get("error") or "O OLMo não está disponível.")
        result = await GRAPH.ainvoke(state)
        db.update_run(run_id, status="done", final=result.get("final", ""))
        db.add_run_event(run_id, "Concluído")
    except Exception as err:  # noqa: BLE001
        db.update_run(run_id, status="error", error=str(err))
        db.add_run_event(run_id, f"Erro: {err}")
    await _notify_webhook(run_id)


async def _notify_webhook(run_id: str) -> None:
    if not settings.run_webhook_url:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(settings.run_webhook_url, json=db.get_run(run_id))
    except httpx.HTTPError as err:
        db.add_run_event(run_id, f"Webhook falhou: {err}")
