#!/usr/bin/env node
/**
 * Mesma coisa que checar_whatsapp.js, mas como um servidor HTTP local em vez
 * de rodar uma vez sobre um CSV inteiro — pra outros processos (o orquestrador
 * completar_whatsapp.py, por exemplo) poderem checar número por número sem
 * reconectar a sessão do WhatsApp a cada chamada.
 *
 * Reconectar toda hora além de lento reforça o padrão "isso parece um robô"
 * pro WhatsApp — manter UMA sessão viva e reusar é mais seguro pro número.
 *
 * Uso:
 *   node servidor_whatsapp.js
 *   (primeira vez: escaneie o QR igual ao checar_whatsapp.js)
 *
 * Endpoints:
 *   GET /health                  -> { conectado: true/false }
 *   GET /verificar?numero=5511999999999
 *       -> { numero, tem_whatsapp: "sim"|"nao"|"erro", whatsapp_jid, erro }
 *
 * Mesmos avisos do checar_whatsapp.js: use chip secundário, não é API oficial,
 * ./auth_info/ guarda credenciais de sessão (nunca commitar).
 */

const http = require("http");
const fs = require("fs");
const path = require("path");
const { URL } = require("url");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require("@whiskeysockets/baileys");
const pino = require("pino");
const QRCode = require("qrcode");
const qrcodeTerminal = require("qrcode-terminal");

const AUTH_DIR = path.join(__dirname, "auth_info");
const LOGGER = pino({ level: "silent" });
const PORT = parseInt(process.env.WHATSAPP_SERVER_PORT || "3939", 10);

// 4-6s entre checagens (4s + até 2s de variação aleatória), e uma pausa mais
// longa a cada 100 checagens — mais rápido que o padrão anterior (8-13s a
// cada 50), continua com alguma variação em vez de intervalo fixo, mas é
// um ritmo mais arriscado que o conservador. Ajustável via env var.
const DELAY_MS = parseInt(process.env.WHATSAPP_SERVER_DELAY_MS || "4000", 10);
const JITTER_MAX_MS = parseInt(process.env.WHATSAPP_SERVER_JITTER_MS || "2000", 10);
const PAUSA_LONGA_A_CADA = parseInt(process.env.WHATSAPP_SERVER_PAUSA_A_CADA || "100", 10);
const PAUSA_LONGA_MS = parseInt(process.env.WHATSAPP_SERVER_PAUSA_LONGA_MS || "60000", 10);

let sockAtual = null;
let filaOcupada = false;
let checagensDesdeAPausa = 0;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizaParaJid(numeroBruto) {
  const digitos = String(numeroBruto || "").replace(/\D/g, "");
  if (!digitos) return null;
  return `${digitos}@s.whatsapp.net`;
}

async function conectar() {
  if (!fs.existsSync(AUTH_DIR)) fs.mkdirSync(AUTH_DIR, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: state,
    logger: LOGGER,
    printQRInTerminal: false,
    browser: ["Prospeccao-Fintech-Server", "Chrome", "1.0"],
  });

  sock.ev.on("creds.update", saveCreds);

  return new Promise((resolve, reject) => {
    sock.ev.on("connection.update", async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        console.log("\nEscaneie o QR code (Aparelhos conectados > Conectar aparelho):\n");
        qrcodeTerminal.generate(qr, { small: true });
        try {
          const qrPath = path.join(__dirname, "qr.png");
          await QRCode.toFile(qrPath, qr, { width: 400 });
          console.log(`QR code também salvo em: ${qrPath}\n`);
        } catch (err) {
          console.error("Não consegui salvar qr.png:", err.message);
        }
      }

      if (connection === "open") {
        console.log("Conectado ao WhatsApp.\n");
        sockAtual = sock;
        resolve(sock);
      }

      if (connection === "close") {
        sockAtual = null;
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        const deveReconectar = statusCode !== DisconnectReason.loggedOut;
        if (statusCode === DisconnectReason.loggedOut) {
          console.error("Sessão desconectada (logout). Apague ./auth_info/ e escaneie o QR code de novo.");
          reject(new Error("logged_out"));
        } else if (deveReconectar) {
          console.log("Conexão caiu, tentando reconectar...");
          try {
            await conectar();
          } catch (err) {
            console.error("Falha ao reconectar:", err.message);
          }
        }
      }
    });
  });
}

async function checaNumero(numeroBruto) {
  const jid = normalizaParaJid(numeroBruto);
  if (!jid) return { tem_whatsapp: "erro", whatsapp_jid: "", erro: "numero_vazio_ou_invalido" };
  if (!sockAtual) return { tem_whatsapp: "erro", whatsapp_jid: "", erro: "sessao_nao_conectada" };

  try {
    const resultado = await sockAtual.onWhatsApp(jid);
    if (Array.isArray(resultado) && resultado.length > 0 && resultado[0].exists) {
      return { tem_whatsapp: "sim", whatsapp_jid: resultado[0].jid || "", erro: "" };
    }
    return { tem_whatsapp: "nao", whatsapp_jid: "", erro: "" };
  } catch (err) {
    return { tem_whatsapp: "erro", whatsapp_jid: "", erro: String(err.message || err) };
  }
}

// Fila simples: uma checagem por vez. O ritmo é controlado por
// `proximoHorarioPermitido` (relógio de parede), NÃO por "tem mais coisa na
// fila" — um cliente sequencial (pede um número, espera a resposta, só
// então pede o próximo, como completar_whatsapp.py) nunca deixa mais de um
// item na fila ao mesmo tempo, e uma versão anterior deste código só
// aplicava o delay quando havia mais de um item enfileirado — na prática,
// o delay NUNCA rodava contra um cliente sequencial. Usar um horário-limite
// absoluto corrige isso: a próxima checagem espera até esse horário mesmo
// que a fila tenha ficado vazia entre uma chamada e outra.
const fila = [];
let proximoHorarioPermitido = 0;

async function processaFila() {
  if (filaOcupada) return;
  filaOcupada = true;
  while (fila.length > 0) {
    const { numero, resolve } = fila.shift();

    const espera = proximoHorarioPermitido - Date.now();
    if (espera > 0) await sleep(espera);

    const resultado = await checaNumero(numero);
    checagensDesdeAPausa++;

    if (checagensDesdeAPausa >= PAUSA_LONGA_A_CADA) {
      const pausa = PAUSA_LONGA_MS + Math.floor(Math.random() * PAUSA_LONGA_MS * 0.3);
      console.log(`[pausa longa] ${checagensDesdeAPausa} checagens feitas, pausando ${Math.round(pausa / 1000)}s antes da próxima...`);
      checagensDesdeAPausa = 0;
      proximoHorarioPermitido = Date.now() + pausa;
    } else {
      const jitter = Math.floor(Math.random() * JITTER_MAX_MS);
      proximoHorarioPermitido = Date.now() + DELAY_MS + jitter;
    }

    resolve(resultado);
  }
  filaOcupada = false;
}

function enfileiraChecagem(numero) {
  return new Promise((resolve) => {
    fila.push({ numero, resolve });
    processaFila();
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (url.pathname === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ conectado: sockAtual !== null, fila: fila.length, checagens_desde_a_pausa: checagensDesdeAPausa }));
    return;
  }

  if (url.pathname === "/verificar") {
    const numero = url.searchParams.get("numero");
    if (!numero) {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ erro: "parametro 'numero' obrigatorio" }));
      return;
    }
    const resultado = await enfileiraChecagem(numero);
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ numero, ...resultado }));
    return;
  }

  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ erro: "rota nao encontrada", rotas: ["/health", "/verificar?numero=..."] }));
});

async function main() {
  // Sobe o servidor já — /health reporta "conectado: false" até o WhatsApp
  // autenticar, em vez de deixar o processo inteiro mudo até alguém escanear.
  server.listen(PORT, () => {
    console.log(`Servidor de checagem de WhatsApp rodando em http://localhost:${PORT}`);
    console.log(`  GET /health`);
    console.log(`  GET /verificar?numero=5511999999999`);
    console.log("\nConectando ao WhatsApp (primeira vez precisa escanear QR code)...");
  });

  try {
    await conectar();
  } catch (err) {
    console.error("Erro ao conectar ao WhatsApp:", err.message);
  }
}

main().catch((err) => {
  console.error("Erro fatal:", err);
  process.exit(1);
});
