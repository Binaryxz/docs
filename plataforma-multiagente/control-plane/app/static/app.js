// UI da plataforma: JavaScript puro, sem build.

const state = { agents: [], selected: null, view: "chat" };

function token() {
  try { return localStorage.getItem("adminToken") || ""; } catch { return ""; }
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) {
    const value = prompt("Token de administrador (ADMIN_TOKEN):");
    if (value !== null) {
      try { localStorage.setItem("adminToken", value); } catch {}
      return api(path, options);
    }
  }
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  const type = resp.headers.get("content-type") || "";
  return type.includes("application/json") ? resp.json() : resp.text();
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child != null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR") : "");
const short = (addr) => (addr ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : "—");

// ─── Status ──────────────────────────────────────────────────────────

async function loadStatus() {
  const box = document.getElementById("status");
  try {
    const s = await api("/api/status");
    box.replaceChildren(
      el("span", {}, el("span", { class: `dot ${s.llm.ok ? "ok" : "err"}` }), `LLM: ${s.llm.configured}`),
      el("span", {}, el("span", { class: `dot ${s.runner.ok ? "ok" : "warn"}` }), `Automaton runner: ${s.runner.ok ? "online" : "offline"}`),
    );
  } catch (err) {
    box.textContent = `erro: ${err.message}`;
  }
}

// ─── Agentes ─────────────────────────────────────────────────────────

async function loadTemplates() {
  const templates = await api("/api/templates");
  const form = document.getElementById("agent-form");
  document.getElementById("templates").replaceChildren(
    ...templates.map((t) =>
      el("button", {
        type: "button",
        onclick: () => {
          form.name.value = t.name;
          form.role.value = t.role;
          form.genesis_prompt.value = t.genesis_prompt;
        },
      }, t.name),
    ),
  );
}

function agentBadge(agent) {
  const rt = agent.runtime;
  if (!rt) return el("span", { class: "badge" }, "só LLM");
  if (rt.running) return el("span", { class: "badge" }, el("span", { class: "dot ok" }), "rodando");
  return el("span", { class: "badge" }, el("span", { class: "dot" }), rt.desired === "running" ? "reiniciando" : "parado");
}

async function agentAction(id, action) {
  try {
    if (action === "delete") {
      if (!confirm("Remover o agente e todo o estado dele (carteira, memória)?")) return;
      await api(`/api/agents/${id}`, { method: "DELETE" });
      if (state.selected === id) closeDetail();
    } else {
      await api(`/api/agents/${id}/${action}`, { method: "POST" });
    }
  } catch (err) {
    alert(err.message);
  }
  loadAgents();
}

async function loadAgents() {
  state.agents = await api("/api/agents");
  const list = document.getElementById("agents");
  if (!state.agents.length) {
    list.replaceChildren(el("p", { class: "muted" }, "Nenhum agente ainda."));
  } else {
    list.replaceChildren(
      ...state.agents.map((a) => {
        const usage = a.usage ? `${a.usage.calls} chamadas · ${(a.usage.prompt_tokens || 0) + (a.usage.completion_tokens || 0)} tokens` : "sem chamadas ao LLM";
        const running = a.runtime?.running;
        return el("div", { class: "agent" },
          el("div", { class: "agent-head" }, el("span", { class: "agent-name" }, a.name), agentBadge(a)),
          el("div", { class: "agent-meta" }, `${a.role || "generalista"} · carteira ${short(a.address)} · ${usage}${a.pending ? ` · ${a.pending} msg na fila` : ""}`),
          el("div", { class: "agent-actions" },
            el("button", { onclick: () => openDetail(a.id) }, "Abrir"),
            a.runtime ? el("button", { onclick: () => agentAction(a.id, running ? "stop" : "start") }, running ? "Parar" : "Iniciar") : null,
            el("button", { class: "danger", onclick: () => agentAction(a.id, "delete") }, "Remover"),
          ),
        );
      }),
    );
  }
  renderTeamChecklist();
}

document.getElementById("agent-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const created = await api("/api/agents", {
      method: "POST",
      body: JSON.stringify({
        name: form.name.value,
        role: form.role.value,
        genesis_prompt: form.genesis_prompt.value,
        start: form.start.checked,
      }),
    });
    if (created.warning) alert(`Agente criado, mas o runner do Automaton não respondeu:\n${created.warning}`);
    form.reset();
    form.start.checked = true;
    await loadAgents();
  } catch (err) {
    alert(err.message);
  } finally {
    button.disabled = false;
  }
});

// ─── Detalhe: conversa e logs ────────────────────────────────────────

function openDetail(id) {
  state.selected = id;
  const agent = state.agents.find((a) => a.id === id);
  document.getElementById("detail-title").textContent = agent ? agent.name : id;
  document.getElementById("agent-detail").hidden = false;
  refreshDetail();
  document.getElementById("agent-detail").scrollIntoView({ behavior: "smooth" });
}

function closeDetail() {
  state.selected = null;
  document.getElementById("agent-detail").hidden = true;
}

async function refreshDetail() {
  if (!state.selected) return;
  const id = state.selected;
  if (state.view === "chat") {
    const messages = await api(`/api/agents/${id}/messages`);
    const chat = document.getElementById("chat");
    const atBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 40;
    chat.replaceChildren(
      ...(messages.length
        ? messages.map((m) =>
            el("div", { class: `bubble ${m.direction}` }, m.content,
              el("small", {}, `${m.direction === "to_agent" ? "para o agente" : "do agente"} · ${fmtTime(m.created_at)}${m.direction === "to_agent" && !m.delivered_at ? " · aguardando entrega" : ""}`)),
          )
        : [el("p", { class: "muted" }, "Sem mensagens.")]),
    );
    if (atBottom) chat.scrollTop = chat.scrollHeight;
  } else {
    const logs = document.getElementById("view-logs");
    try {
      logs.textContent = (await api(`/api/agents/${id}/logs?lines=300`)) || "(sem logs)";
    } catch (err) {
      logs.textContent = err.message;
    }
    logs.scrollTop = logs.scrollHeight;
  }
}

document.querySelectorAll(".seg button").forEach((button) =>
  button.addEventListener("click", () => {
    state.view = button.dataset.view;
    document.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("active", b === button));
    document.getElementById("view-chat").hidden = state.view !== "chat";
    document.getElementById("view-logs").hidden = state.view !== "logs";
    refreshDetail();
  }),
);

document.getElementById("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = event.target.content;
  try {
    await api(`/api/agents/${state.selected}/messages`, { method: "POST", body: JSON.stringify({ content: input.value }) });
    input.value = "";
    refreshDetail();
  } catch (err) {
    alert(err.message);
  }
});

// ─── Times ───────────────────────────────────────────────────────────

function renderTeamChecklist() {
  const box = document.getElementById("team-agents");
  const checked = new Set([...box.querySelectorAll("input:checked")].map((i) => i.value));
  if (!state.agents.length) {
    box.replaceChildren(el("p", { class: "muted" }, "Crie agentes primeiro."));
    return;
  }
  box.replaceChildren(
    ...state.agents.map((a) =>
      el("label", {}, el("input", { type: "checkbox", value: a.id, checked: checked.has(a.id) }), `${a.name} — ${a.role || "generalista"}`),
    ),
  );
}

async function loadRuns() {
  const runs = await api("/api/runs");
  const box = document.getElementById("runs");
  const open = new Set([...box.querySelectorAll("details[open]")].map((d) => d.dataset.id));
  if (!runs.length) {
    box.replaceChildren(el("p", { class: "muted" }, "Nenhuma execução ainda."));
    return;
  }
  const names = Object.fromEntries(state.agents.map((a) => [a.id, a.name]));
  box.replaceChildren(
    ...runs.map((r) =>
      el("details", { class: "run", "data-id": r.id, open: open.has(r.id) },
        el("summary", {},
          el("span", { class: "task" }, r.task.length > 120 ? `${r.task.slice(0, 120)}…` : r.task),
          el("span", { class: `status-${r.status}` }, r.status),
          el("span", { class: "muted" }, `${r.executor} · ${(r.agent_ids || []).map((id) => names[id] || id).join(", ")}`),
        ),
        r.plan ? el("div", {}, el("h3", {}, "Plano"), el("ol", {}, ...r.plan.map((p) =>
          el("li", {}, `${p.agent_name}: ${p.subtask}`, (r.results || {})[p.step_id] ? " ✓" : "")))) : null,
        el("h3", {}, "Eventos"),
        el("ol", {}, ...(r.events || []).map((e) => el("li", {}, `${fmtTime(e.at)} — ${e.text}`))),
        r.final ? el("div", {}, el("h3", {}, "Entrega final"), el("div", { class: "final" }, r.final)) : null,
        r.error ? el("p", { class: "status-error" }, r.error) : null,
      ),
    ),
  );
}

document.getElementById("run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const agentIds = [...form.querySelectorAll("#team-agents input:checked")].map((i) => i.value);
  if (!agentIds.length) return alert("Selecione ao menos um agente.");
  try {
    await api("/api/runs", {
      method: "POST",
      body: JSON.stringify({ task: form.task.value, agent_ids: agentIds, executor: form.executor.value }),
    });
    form.task.value = "";
    loadRuns();
  } catch (err) {
    alert(err.message);
  }
});

// ─── Abas e atualização periódica ────────────────────────────────────

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${tab.dataset.tab}`));
  }),
);

async function tick() {
  await Promise.allSettled([loadStatus(), loadAgents().then(loadRuns), refreshDetail()]);
}

loadTemplates().catch(() => {});
tick();
setInterval(tick, 5000);
