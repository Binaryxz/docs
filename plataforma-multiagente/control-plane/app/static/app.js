// Painel da plataforma: JavaScript puro, sem build.

const state = { agents: [], selected: null, view: "chat", tab: "agents", wallets: [], network: null, balances: {} };

async function api(path, options = {}) {
  const resp = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  if (resp.status === 401 && !path.includes("/transfer")) {
    location.href = "/login";
    throw new Error("sessão expirada");
  }
  const type = resp.headers.get("content-type") || "";
  const body = type.includes("application/json") ? await resp.json() : await resp.text();
  if (!resp.ok) throw new Error((body && body.detail) || (typeof body === "string" ? body : `erro ${resp.status}`));
  return body;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child != null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const $ = (id) => document.getElementById(id);
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR") : "");
const short = (addr) => (addr ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : "—");
const explorer = () => (state.network?.network === "sepolia" ? "https://sepolia.etherscan.io" : "https://etherscan.io");

// ─── Fundo: chuva de binários (decorativa) ───────────────────────────

(function binaryRain() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const field = $("binfield");
  const cols = Math.min(36, Math.floor(innerWidth / 38));
  for (let i = 0; i < cols; i++) {
    const s = document.createElement("span");
    s.textContent = Array.from({ length: 40 }, () => (Math.random() > 0.5 ? "1" : "0")).join("");
    s.style.left = `${(i / cols) * 100 + Math.random() * 2}%`;
    s.style.animationDuration = `${14 + Math.random() * 18}s`;
    s.style.animationDelay = `${-Math.random() * 30}s`;
    field.append(s);
  }
})();

// ─── Sessão e status ─────────────────────────────────────────────────

$("logout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/login";
});

async function loadMe() {
  try { const me = await api("/api/me"); $("who").textContent = me.user ? `@${me.user}` : ""; } catch {}
}

function setLlmOffline(reason) {
  let banner = $("llm-banner");
  if (reason) {
    if (!banner) {
      banner = el("div", { id: "llm-banner", class: "banner" });
      document.querySelector("main").prepend(banner);
    }
    banner.textContent = `OLMo offline — ${reason}`;
  } else if (banner) {
    banner.remove();
  }
  const runBtn = document.querySelector("#run-form button[type=submit]");
  if (runBtn) {
    runBtn.disabled = Boolean(reason);
    runBtn.title = reason ? "conecte o OLMo para executar" : "";
  }
}

async function loadStatus() {
  try {
    const s = await api("/api/status");
    $("status").replaceChildren(
      el("span", {}, el("span", { class: `dot ${s.llm.ok ? "ok" : "err"}` }), `olmo: ${s.llm.configured}`),
      el("span", {}, el("span", { class: `dot ${s.runner.ok ? "ok" : "warn"}` }), `automaton: ${s.runner.ok ? "online" : "offline"}`),
    );
    setLlmOffline(s.llm.ok ? "" : (s.llm.error || "sem resposta"));
  } catch (err) {
    $("status").textContent = `erro: ${err.message}`;
  }
}

// ─── Abas ────────────────────────────────────────────────────────────

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    state.tab = tab.dataset.tab;
    document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
    document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${state.tab}`));
    if (state.tab === "wallets") loadWallets(true);
  }),
);

// ─── Agentes ─────────────────────────────────────────────────────────

async function loadTemplates() {
  const templates = await api("/api/templates");
  $("templates").replaceChildren(
    ...templates.map((t) =>
      el("button", { type: "button", class: "chip", onclick: () => { $("a-name").value = t.name; $("a-role").value = t.role; $("a-genesis").value = t.genesis_prompt; } }, `+ ${t.name}`),
    ),
  );
}

function agentBadge(a) {
  const rt = a.runtime;
  if (!rt) return el("span", { class: "badge" }, "só olmo");
  if (rt.running) return el("span", { class: "badge" }, el("span", { class: "dot ok" }), "rodando");
  return el("span", { class: "badge" }, el("span", { class: "dot" }), rt.desired === "running" ? "reiniciando" : "parado");
}

async function agentAction(id, action, button) {
  try {
    if (action === "delete") {
      if (!button.dataset.armed) {
        button.dataset.armed = "1";
        button.textContent = "Confirmar remoção";
        setTimeout(() => { delete button.dataset.armed; button.textContent = "Remover"; }, 4000);
        return;
      }
      await api(`/api/agents/${id}`, { method: "DELETE" });
      if (state.selected === id) closeDetail();
    } else {
      await api(`/api/agents/${id}/${action}`, { method: "POST" });
    }
  } catch (err) {
    alertMsg(err.message);
  }
  loadAgents();
}

function alertMsg(text) {
  $("status").prepend(el("span", { class: "st-error" }, text, " "));
}

async function loadAgents() {
  state.agents = await api("/api/agents");
  const list = $("agents");
  if (!state.agents.length) {
    list.replaceChildren(el("p", { class: "dim" }, "nenhum agente. use um modelo ao lado ou escreva o seu."));
  } else {
    list.replaceChildren(
      ...state.agents.map((a) => {
        const tokens = a.usage ? (a.usage.prompt_tokens || 0) + (a.usage.completion_tokens || 0) : 0;
        const running = a.runtime?.running;
        return el("div", { class: "item" },
          el("div", { class: "row" }, el("span", { class: "name" }, a.name), agentBadge(a)),
          el("div", { class: "meta" }, `${a.role || "generalista"} · carteira ${short(a.address)} · ${a.usage ? `${a.usage.calls} chamadas · ${tokens} tokens` : "sem chamadas"}${a.pending ? ` · ${a.pending} na fila` : ""}`),
          el("div", { class: "actions" },
            el("button", { class: "btn small", onclick: () => openDetail(a.id) }, "Abrir"),
            a.runtime ? el("button", { class: "btn small", onclick: (e) => agentAction(a.id, running ? "stop" : "start", e.currentTarget) }, running ? "Parar" : "Iniciar") : null,
            el("button", { class: "btn small danger", onclick: (e) => agentAction(a.id, "delete", e.currentTarget) }, "Remover"),
          ),
        );
      }),
    );
  }
  renderTeamChecklist();
}

$("agent-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const created = await api("/api/agents", {
      method: "POST",
      body: JSON.stringify({ name: $("a-name").value, role: $("a-role").value, genesis_prompt: $("a-genesis").value, start: $("a-start").checked }),
    });
    if (created.warning) alertMsg(`agente criado sem o runner: ${created.warning}`);
    event.target.reset();
    $("a-start").checked = true;
    await loadAgents();
  } catch (err) {
    alertMsg(err.message);
  } finally {
    button.disabled = false;
  }
});

// ─── Detalhe: conversa e logs ────────────────────────────────────────

function openDetail(id) {
  state.selected = id;
  const agent = state.agents.find((a) => a.id === id);
  $("detail-title").textContent = agent ? agent.name : id;
  $("agent-detail").hidden = false;
  refreshDetail();
  $("agent-detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeDetail() {
  state.selected = null;
  $("agent-detail").hidden = true;
}

async function refreshDetail() {
  if (!state.selected || state.tab !== "agents") return;
  const id = state.selected;
  if (state.view === "chat") {
    const messages = await api(`/api/agents/${id}/messages`);
    const chat = $("chat");
    const atBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 40;
    chat.replaceChildren(
      ...(messages.length
        ? messages.map((m) =>
            el("div", { class: `bubble ${m.direction}` }, m.content,
              el("small", {}, `${m.direction === "to_agent" ? "→ agente" : "← agente"} · ${fmtTime(m.created_at)}${m.direction === "to_agent" && !m.delivered_at ? " · aguardando entrega" : ""}`)))
        : [el("p", { class: "dim" }, "sem mensagens.")]),
    );
    if (atBottom) chat.scrollTop = chat.scrollHeight;
  } else {
    const logs = $("pane-logs");
    try { logs.textContent = (await api(`/api/agents/${id}/logs?lines=300`)) || "(sem logs)"; } catch (err) { logs.textContent = err.message; }
    logs.scrollTop = logs.scrollHeight;
  }
}

document.querySelectorAll(".seg .btn").forEach((button) =>
  button.addEventListener("click", () => {
    state.view = button.dataset.view;
    document.querySelectorAll(".seg .btn").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    $("pane-chat").hidden = state.view !== "chat";
    $("pane-logs").hidden = state.view !== "logs";
    refreshDetail();
  }),
);

$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("chat-input");
  try {
    await api(`/api/agents/${state.selected}/messages`, { method: "POST", body: JSON.stringify({ content: input.value }) });
    input.value = "";
    refreshDetail();
  } catch (err) {
    alertMsg(err.message);
  }
});

// ─── Times ───────────────────────────────────────────────────────────

function renderTeamChecklist() {
  const box = $("team-agents");
  const checked = new Set([...box.querySelectorAll("input:checked")].map((i) => i.value));
  if (!state.agents.length) { box.replaceChildren(el("p", { class: "dim" }, "crie agentes primeiro.")); return; }
  box.replaceChildren(
    ...state.agents.map((a) =>
      el("label", { class: "check", for: `tm-${a.id}` }, el("input", { type: "checkbox", id: `tm-${a.id}`, value: a.id, checked: checked.has(a.id) }), `${a.name} — ${a.role || "generalista"}`)),
  );
}

async function loadRuns() {
  if (state.tab !== "teams") return;
  const runs = await api("/api/runs");
  const box = $("runs");
  const open = new Set([...box.querySelectorAll("details[open]")].map((d) => d.dataset.id));
  if (!runs.length) { box.replaceChildren(el("p", { class: "dim" }, "nenhuma execução.")); return; }
  const names = Object.fromEntries(state.agents.map((a) => [a.id, a.name]));
  box.replaceChildren(
    ...runs.map((r) =>
      el("details", { class: "run item", "data-id": r.id, open: open.has(r.id) },
        el("summary", {},
          el("span", { class: "task" }, r.task.length > 110 ? `${r.task.slice(0, 110)}…` : r.task),
          el("span", { class: `st-${r.status}` }, r.status),
          el("span", { class: "meta" }, `${r.executor} · ${(r.agent_ids || []).map((id) => names[id] || id).join(", ")}`)),
        r.plan ? el("div", {}, el("h3", {}, "plano"), el("ol", {}, ...r.plan.map((p) => el("li", {}, `${p.agent_name}: ${p.subtask}`, (r.results || {})[p.step_id] ? " ✓" : "")))) : null,
        el("h3", {}, "eventos"),
        el("ol", {}, ...(r.events || []).map((e) => el("li", {}, `${fmtTime(e.at)} — ${e.text}`))),
        r.final ? el("div", {}, el("h3", {}, "entrega final"), el("div", { class: "final" }, r.final)) : null,
        r.error ? el("p", { class: "st-error" }, r.error) : null,
      )),
  );
}

$("run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const agentIds = [...document.querySelectorAll("#team-agents input:checked")].map((i) => i.value);
  if (!agentIds.length) { alertMsg("selecione ao menos um agente."); return; }
  try {
    await api("/api/runs", { method: "POST", body: JSON.stringify({ task: $("r-task").value, agent_ids: agentIds, executor: $("r-exec").value }) });
    $("r-task").value = "";
    loadRuns();
  } catch (err) {
    alertMsg(err.message);
  }
});

// ─── Carteiras ───────────────────────────────────────────────────────

async function loadWallets(withBalances = false) {
  try {
    const data = await api("/api/wallets");
    state.wallets = data.wallets;
    state.network = data.network;
  } catch (err) {
    $("wallets").replaceChildren(el("p", { class: "st-error" }, err.message));
    return;
  }
  const n = state.network;
  $("netbar").replaceChildren(
    el("span", {}, el("span", { class: `dot ${n.network === "mainnet" ? "warn" : "ok"}` }), `rede: ${n.network} (chain ${n.chainId})`),
    el("span", { class: "dim" }, `usdt: ${n.usdtContract ? short(n.usdtContract) : "não configurado"}`),
    el("span", { class: "dim" }, `limite por envio: ${n.limits.usdtPerTx} USDT · ${n.limits.ethPerTx} ETH`),
    n.network === "mainnet" ? el("span", { class: "warn" }, "dinheiro real") : null,
  );
  for (const sel of [$("t-from"), $("s-from")]) {
    const current = sel.value;
    sel.replaceChildren(...state.wallets.map((w) => el("option", { value: w.id, selected: w.id === current }, `${w.label} · ${short(w.address)}`)));
  }
  renderWallets();
  if (withBalances) refreshBalances();
  loadTxs();
}

function renderWallets() {
  const box = $("wallets");
  if (!state.wallets.length) { box.replaceChildren(el("p", { class: "dim" }, "nenhuma carteira. gere uma ao lado.")); return; }
  box.replaceChildren(
    ...state.wallets.map((w) => {
      const b = state.balances[w.id];
      return el("div", { class: "item" },
        el("div", { class: "row" }, el("span", { class: "name" }, w.label), el("span", { class: "badge" }, w.kind === "agent" ? "agente" : "tesouraria")),
        el("div", { class: "row" },
          el("span", { class: "addr" }, w.address),
          el("button", { class: "btn small", onclick: (e) => copy(w.address, e.currentTarget) }, "Copiar")),
        el("div", { class: "bal" },
          b?.error ? el("span", { class: "st-error" }, b.error)
            : b ? [el("span", {}, el("b", {}, b.usdt ?? "—"), " USDT"), el("span", {}, el("b", {}, Number(b.eth).toFixed(6)), " ETH")]
            : el("span", { class: "dim" }, "saldo: —")),
      );
    }),
  );
}

async function copy(text, button) {
  try { await navigator.clipboard.writeText(text); button.textContent = "Copiado"; } catch { button.textContent = "Selecione e copie"; }
  setTimeout(() => (button.textContent = "Copiar"), 2000);
}

async function refreshBalances() {
  await Promise.all(state.wallets.map(async (w) => {
    try { state.balances[w.id] = await api(`/api/wallets/${encodeURIComponent(w.id)}/balance`); }
    catch (err) { state.balances[w.id] = { error: err.message }; }
  }));
  renderWallets();
}

async function loadTxs() {
  const txs = await api("/api/transactions").catch(() => []);
  const box = $("txs");
  if (!txs.length) { box.replaceChildren(el("p", { class: "dim" }, "nenhuma transação.")); return; }
  box.replaceChildren(
    ...txs.map((t) =>
      el("div", { class: "item" },
        el("div", { class: "row" }, el("span", {}, el("b", {}, `${t.amount} ${t.asset}`), ` → ${short(t.to_addr)}`), el("span", { class: `st-${t.status}` }, t.status)),
        el("div", { class: "meta" }, `${fmtTime(t.created_at)} · de ${short(t.from_addr)} · `,
          el("a", { href: `${explorer()}/tx/${t.hash}`, target: "_blank", rel: "noopener" }, short(t.hash))),
      )),
  );
}

$("w-refresh").addEventListener("click", () => refreshBalances());

$("wallet-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/wallets", { method: "POST", body: JSON.stringify({ label: $("w-label").value }) });
    loadWallets(true);
  } catch (err) {
    alertMsg(err.message);
  }
});

// Envio em dois passos: "Revisar" mostra o resumo; o segundo clique assina e envia.
let pending = null;
function resetTransfer() {
  pending = null;
  $("t-confirm").hidden = true;
  $("t-btn").textContent = "Revisar envio";
}
["t-from", "t-to", "t-amount", "t-asset"].forEach((id) => $(id).addEventListener("input", resetTransfer));

$("transfer-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const msg = $("t-msg");
  const body = { to: $("t-to").value.trim(), amount: $("t-amount").value.trim(), asset: $("t-asset").value };
  const from = state.wallets.find((w) => w.id === $("t-from").value);
  if (!pending) {
    pending = body;
    $("t-confirm").textContent = `Confirme o envio na ${state.network?.network}:\n${body.amount} ${body.asset}\nde   ${from?.label} (${from?.address})\npara ${body.to}\nA transação é irreversível.`;
    $("t-confirm").hidden = false;
    $("t-btn").textContent = "Assinar e enviar";
    msg.textContent = "";
    return;
  }
  $("t-btn").disabled = true;
  msg.className = "msg";
  msg.textContent = "assinando e enviando…";
  try {
    const tx = await api(`/api/wallets/${encodeURIComponent($("t-from").value)}/transfer`, {
      method: "POST",
      body: JSON.stringify({ ...pending, password: $("t-pass").value }),
    });
    msg.className = "msg ok";
    msg.replaceChildren("enviado: ", el("a", { href: `${explorer()}/tx/${tx.hash}`, target: "_blank", rel: "noopener" }, tx.hash));
    $("t-to").value = ""; $("t-amount").value = "";
    loadTxs();
    setTimeout(refreshBalances, 15000);
  } catch (err) {
    msg.className = "msg err";
    msg.textContent = err.message;
  } finally {
    $("t-pass").value = "";
    $("t-btn").disabled = false;
    resetTransfer();
  }
});

$("sign-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const out = $("s-out");
  try {
    const r = await api(`/api/wallets/${encodeURIComponent($("s-from").value)}/sign`, { method: "POST", body: JSON.stringify({ message: $("s-msg").value }) });
    out.textContent = `endereço:   ${r.address}\nmensagem:   ${r.message}\nassinatura: ${r.signature}`;
  } catch (err) {
    out.textContent = err.message;
  }
  out.hidden = false;
});

// ─── Atualização periódica ───────────────────────────────────────────

async function tick() {
  await Promise.allSettled([loadStatus(), loadAgents().then(loadRuns), refreshDetail()]);
}

loadMe();
loadTemplates().catch(() => {});
tick();
setInterval(tick, 5000);
