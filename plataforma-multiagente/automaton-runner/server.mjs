// Runner multi-agente para o Conway Automaton.
//
// Roda N processos `automaton --run` dentro de um único container, cada um com
// seu próprio HOME (= ~/.automaton isolado: carteira, SQLite, SOUL.md, skills).
//
// Expõe duas portas:
//   - API interna (RUNNER_PORT, padrão 7000): o control plane cria/para/lista agentes.
//   - Túnel HTTPS em 127.0.0.1:8443: o Automaton só aceita HTTPS para a API
//     Conway, o relay social e o Ollama. Este túnel termina TLS com um
//     certificado autoassinado (confiado via NODE_EXTRA_CA_CERTS) e repassa
//     tudo para o control plane, que emula a Conway Cloud localmente.

import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import path from "node:path";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import * as wallets from "./wallets.mjs";

const AUTOMATON_DIR = process.env.AUTOMATON_DIR || "/opt/automaton";
const DATA_DIR = process.env.DATA_DIR || "/data";
const AGENTS_DIR = path.join(DATA_DIR, "agents");
const TLS_DIR = path.join(DATA_DIR, "tls");
const CONTROL_PLANE_URL = (process.env.CONTROL_PLANE_URL || "http://control-plane:8000").replace(/\/$/, "");
const RUNNER_TOKEN = process.env.RUNNER_TOKEN || "";
const RUNNER_PORT = Number(process.env.RUNNER_PORT || 7000);
const TUNNEL_PORT = Number(process.env.TUNNEL_PORT || 8443);
const RESTART_DELAY_MS = 5_000;

const requireFromAutomaton = createRequire(path.join(AUTOMATON_DIR, "package.json"));
const { generatePrivateKey, privateKeyToAccount } = requireFromAutomaton("viem/accounts");

fs.mkdirSync(AGENTS_DIR, { recursive: true });

/** @type {Map<string, {proc: import("node:child_process").ChildProcess | null, restarts: number, startedAt: string | null}>} */
const procs = new Map();

const log = (...args) => console.log(new Date().toISOString(), "[runner]", ...args);

// ─── Preparação do diretório do agente ───────────────────────────────

function agentHome(id) {
  if (!/^[a-z0-9-]{3,64}$/.test(id)) throw new Error(`id inválido: ${id}`);
  return path.join(AGENTS_DIR, id);
}

function readMeta(id) {
  const file = path.join(agentHome(id), "meta.json");
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf-8")) : null;
}

function writeMeta(id, meta) {
  fs.writeFileSync(path.join(agentHome(id), "meta.json"), JSON.stringify(meta, null, 2));
}

function heartbeatYaml() {
  // Inbox a cada minuto; tarefas que dependem da blockchain/GitHub desligadas.
  return [
    "defaultIntervalMs: 30000",
    "lowComputeMultiplier: 1",
    "entries:",
    "  - { name: check_social_inbox, schedule: '* * * * *', task: check_social_inbox, enabled: true }",
    "  - { name: check_usdc_balance, schedule: '0 0 1 1 *', task: check_usdc_balance, enabled: false }",
    "  - { name: check_for_updates, schedule: '0 0 1 1 *', task: check_for_updates, enabled: false }",
    "",
  ].join("\n");
}

function soulMd(spec, address) {
  return `# ${spec.name}\n\nEndereço: ${address}\nCriador: ${spec.creatorAddress}\n\n## Papel\n${spec.role || "agente generalista"}\n\n## Propósito\n${spec.genesisPrompt}\n`;
}

function prepareAgent(spec) {
  const home = agentHome(spec.id);
  const dotDir = path.join(home, ".automaton");
  fs.mkdirSync(path.join(dotDir, "skills"), { recursive: true, mode: 0o700 });

  const walletFile = path.join(dotDir, "wallet.json");
  if (!fs.existsSync(walletFile)) {
    const wallet = { privateKey: generatePrivateKey(), createdAt: new Date().toISOString(), chainType: "evm" };
    fs.writeFileSync(walletFile, JSON.stringify(wallet, null, 2), { mode: 0o600 });
  }
  const { privateKey } = JSON.parse(fs.readFileSync(walletFile, "utf-8"));
  const address = privateKeyToAccount(privateKey).address;

  // Todas as URLs "Conway" apontam para o túnel local, com o id do agente no
  // caminho: assim o control plane sabe quem está chamando sem verificar assinatura.
  const base = `https://localhost:${TUNNEL_PORT}/a/${spec.id}`;
  const model = spec.model;
  const config = {
    name: spec.name,
    genesisPrompt: spec.genesisPrompt,
    creatorMessage: spec.role || "",
    creatorAddress: spec.creatorAddress,
    registeredWithConway: true,
    sandboxId: "", // vazio = execução local (sem sandbox Conway)
    conwayApiUrl: base,
    conwayApiKey: `local-${spec.id}`,
    ollamaBaseUrl: base,
    socialRelayUrl: base,
    inferenceModel: model,
    maxTokensPerTurn: spec.maxTokensPerTurn || 4096,
    maxTurnsPerCycle: spec.maxTurnsPerCycle || 15,
    heartbeatConfigPath: "~/.automaton/heartbeat.yml",
    dbPath: "~/.automaton/state.db",
    logLevel: "info",
    walletAddress: address,
    version: "0.2.1",
    skillsDir: "~/.automaton/skills",
    maxChildren: 0, // replicação depende de sandboxes da Conway Cloud
    chainType: "evm",
    modelStrategy: {
      inferenceModel: model,
      lowComputeModel: model,
      criticalModel: model,
      maxTokensPerTurn: spec.maxTokensPerTurn || 4096,
      enableModelFallback: false,
    },
  };
  fs.writeFileSync(path.join(dotDir, "automaton.json"), JSON.stringify(config, null, 2), { mode: 0o600 });
  fs.writeFileSync(path.join(dotDir, "heartbeat.yml"), heartbeatYaml());

  const soulFile = path.join(dotDir, "SOUL.md");
  if (!fs.existsSync(soulFile)) fs.writeFileSync(soulFile, soulMd(spec, address), { mode: 0o600 });

  const constitutionSrc = path.join(AUTOMATON_DIR, "constitution.md");
  const constitutionDst = path.join(dotDir, "constitution.md");
  if (fs.existsSync(constitutionSrc) && !fs.existsSync(constitutionDst)) {
    fs.copyFileSync(constitutionSrc, constitutionDst);
    fs.chmodSync(constitutionDst, 0o444);
  }

  writeMeta(spec.id, { ...spec, address, desired: "running" });
  return address;
}

// ─── Ciclo de vida dos processos ─────────────────────────────────────

function startAgent(id) {
  const current = procs.get(id);
  if (current?.proc && current.proc.exitCode === null) return;

  const home = agentHome(id);
  const logStream = fs.createWriteStream(path.join(home, "agent.log"), { flags: "a" });
  const proc = spawn(process.execPath, [path.join(AUTOMATON_DIR, "dist/index.js"), "--run"], {
    cwd: home,
    env: {
      PATH: process.env.PATH,
      HOME: home,
      NODE_EXTRA_CA_CERTS: path.join(TLS_DIR, "cert.pem"),
      NODE_ENV: "production",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  proc.stdout.pipe(logStream);
  proc.stderr.pipe(logStream);

  const entry = { proc, restarts: current?.restarts ?? 0, startedAt: new Date().toISOString() };
  procs.set(id, entry);
  log(`agente ${id} iniciado (pid ${proc.pid})`);

  proc.on("exit", (code, signal) => {
    log(`agente ${id} saiu (code=${code}, signal=${signal})`);
    const meta = readMeta(id);
    if (meta?.desired === "running") {
      entry.restarts += 1;
      setTimeout(() => startAgent(id), RESTART_DELAY_MS);
    }
  });
}

function stopAgent(id) {
  const meta = readMeta(id);
  if (meta) writeMeta(id, { ...meta, desired: "stopped" });
  const entry = procs.get(id);
  if (entry?.proc && entry.proc.exitCode === null) entry.proc.kill("SIGTERM");
}

function agentStatus(id) {
  const meta = readMeta(id);
  if (!meta) return null;
  const entry = procs.get(id);
  const running = Boolean(entry?.proc && entry.proc.exitCode === null);
  return {
    id,
    name: meta.name,
    address: meta.address,
    model: meta.model,
    desired: meta.desired,
    running,
    pid: running ? entry.proc.pid : null,
    restarts: entry?.restarts ?? 0,
    startedAt: entry?.startedAt ?? null,
  };
}

function tailLog(id, lines) {
  const file = path.join(agentHome(id), "agent.log");
  if (!fs.existsSync(file)) return "";
  const stat = fs.statSync(file);
  const size = Math.min(stat.size, 256 * 1024);
  const fd = fs.openSync(file, "r");
  const buf = Buffer.alloc(size);
  fs.readSync(fd, buf, 0, size, stat.size - size);
  fs.closeSync(fd);
  return buf.toString("utf-8").split("\n").slice(-lines).join("\n");
}

// ─── API interna ─────────────────────────────────────────────────────

function send(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf-8")) : {};
}

const api = http.createServer(async (req, res) => {
  try {
    if (RUNNER_TOKEN && req.headers.authorization !== `Bearer ${RUNNER_TOKEN}`) {
      return send(res, 401, { error: "unauthorized" });
    }
    const url = new URL(req.url, "http://runner");
    const parts = url.pathname.split("/").filter(Boolean); // ["agents", id, action]

    if (req.method === "GET" && url.pathname === "/health") return send(res, 200, { ok: true });

    if (parts[0] === "wallets") {
      if (parts.length === 1 && req.method === "GET") return send(res, 200, { network: wallets.networkInfo(), wallets: wallets.listWallets() });
      if (parts.length === 1 && req.method === "POST") return send(res, 201, wallets.createWallet((await readJson(req)).label));
      if (parts[1] === "tx" && parts[2] && req.method === "GET") return send(res, 200, await wallets.txStatus(parts[2]));
      const walletId = decodeURIComponent(parts[1] || "");
      if (parts[2] === "balance" && req.method === "GET") return send(res, 200, await wallets.balance(walletId));
      if (parts[2] === "sign" && req.method === "POST") return send(res, 200, await wallets.signMessage(walletId, (await readJson(req)).message));
      if (parts[2] === "transfer" && req.method === "POST") return send(res, 200, await wallets.transfer(walletId, await readJson(req)));
      return send(res, 404, { error: "not found" });
    }

    if (parts[0] !== "agents") return send(res, 404, { error: "not found" });

    if (parts.length === 1 && req.method === "GET") {
      const ids = fs.readdirSync(AGENTS_DIR).filter((d) => fs.existsSync(path.join(AGENTS_DIR, d, "meta.json")));
      return send(res, 200, ids.map(agentStatus));
    }

    if (parts.length === 1 && req.method === "POST") {
      const spec = await readJson(req);
      for (const field of ["id", "name", "genesisPrompt", "creatorAddress", "model"]) {
        if (!spec[field]) return send(res, 400, { error: `campo obrigatório: ${field}` });
      }
      const address = prepareAgent(spec);
      if (spec.start !== false) startAgent(spec.id);
      return send(res, 201, { ...agentStatus(spec.id), address });
    }

    const id = parts[1];
    if (!readMeta(id)) return send(res, 404, { error: "agente não encontrado" });

    if (parts.length === 2 && req.method === "GET") return send(res, 200, agentStatus(id));

    if (parts.length === 2 && req.method === "DELETE") {
      stopAgent(id);
      procs.delete(id);
      fs.rmSync(agentHome(id), { recursive: true, force: true });
      return send(res, 200, { deleted: id });
    }

    if (parts[2] === "start" && req.method === "POST") {
      writeMeta(id, { ...readMeta(id), desired: "running" });
      startAgent(id);
      return send(res, 200, agentStatus(id));
    }

    if (parts[2] === "stop" && req.method === "POST") {
      stopAgent(id);
      return send(res, 200, agentStatus(id));
    }

    if (parts[2] === "logs" && req.method === "GET") {
      const lines = Math.min(Number(url.searchParams.get("lines") || 200), 2000);
      res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
      return res.end(tailLog(id, lines));
    }

    return send(res, 404, { error: "not found" });
  } catch (err) {
    log("erro na API:", err?.shortMessage || err?.message || err);
    return send(res, 500, { error: String(err?.shortMessage || err?.message || err) });
  }
});

// ─── Túnel HTTPS → control plane ─────────────────────────────────────

const upstream = new URL(CONTROL_PLANE_URL);
const upstreamLib = upstream.protocol === "https:" ? https : http;

const tunnel = https.createServer(
  {
    key: fs.readFileSync(path.join(TLS_DIR, "key.pem")),
    cert: fs.readFileSync(path.join(TLS_DIR, "cert.pem")),
  },
  (req, res) => {
    const target = new URL(`${upstream.pathname.replace(/\/$/, "")}/conway${req.url}`, upstream);
    const headers = { ...req.headers, host: upstream.host, "x-runner-token": RUNNER_TOKEN };
    const proxied = upstreamLib.request(target, { method: req.method, headers }, (upRes) => {
      res.writeHead(upRes.statusCode || 502, upRes.headers);
      upRes.pipe(res);
    });
    proxied.setTimeout(15 * 60_000);
    proxied.on("error", (err) => {
      log("túnel: erro ao falar com o control plane:", err.message);
      if (!res.headersSent) send(res, 502, { error: "control plane indisponível" });
      else res.end();
    });
    req.pipe(proxied);
  },
);

tunnel.listen(TUNNEL_PORT, "127.0.0.1", () => log(`túnel HTTPS em https://localhost:${TUNNEL_PORT} → ${CONTROL_PLANE_URL}/conway`));
api.listen(RUNNER_PORT, "0.0.0.0", () => log(`API do runner em :${RUNNER_PORT}`));

// Retoma os agentes que estavam rodando antes de um restart do container.
for (const id of fs.readdirSync(AGENTS_DIR)) {
  const meta = fs.existsSync(path.join(AGENTS_DIR, id, "meta.json")) ? readMeta(id) : null;
  if (meta?.desired === "running") {
    prepareAgent(meta); // regrava config caso o modelo/URL tenha mudado
    startAgent(id);
  }
}

for (const sig of ["SIGTERM", "SIGINT"]) {
  process.on(sig, () => {
    for (const { proc } of procs.values()) if (proc && proc.exitCode === null) proc.kill("SIGTERM");
    setTimeout(() => process.exit(0), 3_000);
  });
}
