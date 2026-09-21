#!/usr/bin/env node
/**
 * Verifica se os telefones de uma planilha de leads têm conta ativa no
 * WhatsApp, ANTES do disparo — evita gastar orçamento de outreach em
 * número que não recebe mensagem.
 *
 * Como funciona: usa a própria sessão do WhatsApp Web (biblioteca Baileys),
 * exatamente como quando você escaneia o QR code pra usar o WhatsApp no
 * navegador. Não é a API oficial da Meta — é automação sobre o protocolo do
 * WhatsApp Web, então:
 *
 *   1. É preciso logar UMA VEZ escaneando um QR code com um número de
 *      WhatsApp real (Configurações > Aparelhos conectados > Conectar
 *      aparelho, no app do celular). A sessão fica salva em ./auth_info/ e
 *      não precisa escanear de novo nas próximas execuções.
 *
 *   2. RECOMENDADO usar um chip/número secundário pra isso, não o WhatsApp
 *      principal da empresa. Consultar volume alto de números em pouco
 *      tempo é um padrão que o WhatsApp pode identificar como automação e,
 *      em casos extremos, suspender o número. Este script já roda com
 *      delay entre consultas pra reduzir esse risco, mas o risco não é
 *      zero — é o preço de não pagar por uma API terceira.
 *
 *   3. ./auth_info/ contém as CREDENCIAIS DA SESSÃO (equivalente a estar
 *      logado no WhatsApp Web) — nunca commitar essa pasta no git. Já está
 *      no .gitignore deste diretório.
 *
 * Uso:
 *   npm install
 *   node checar_whatsapp.js entrada.csv saida.csv \
 *     --coluna-telefone telefone_e164 [--limite 20] [--delay-ms 4000]
 *
 * Na primeira vez, um QR code aparece no terminal E é salvo em qr.png —
 * escaneie com o WhatsApp do celular que vai ser usado pra checagem.
 */

const fs = require("fs");
const path = require("path");
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require("@whiskeysockets/baileys");
const pino = require("pino");
const QRCode = require("qrcode");
const qrcodeTerminal = require("qrcode-terminal");
const { parse } = require("csv-parse/sync");
const { stringify } = require("csv-stringify/sync");

const AUTH_DIR = path.join(__dirname, "auth_info");
const LOGGER = pino({ level: "silent" });

function parseArgs(argv) {
  const args = { limite: null, delayMs: 4000, colunaTelefone: "telefone_e164" };
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--coluna-telefone") args.colunaTelefone = argv[++i];
    else if (a === "--limite") args.limite = parseInt(argv[++i], 10);
    else if (a === "--delay-ms") args.delayMs = parseInt(argv[++i], 10);
    else positional.push(a);
  }
  if (positional.length < 2) {
    console.error(
      "Uso: node checar_whatsapp.js entrada.csv saida.csv [--coluna-telefone telefone_e164] [--limite N] [--delay-ms 4000]"
    );
    process.exit(1);
  }
  args.entrada = positional[0];
  args.saida = positional[1];
  return args;
}

function normalizaParaJid(numeroE164) {
  // espera formato +5511999999999 (saída do validar_telefone.py); aceita
  // também sem o "+"
  const digitos = String(numeroE164 || "").replace(/\D/g, "");
  if (!digitos) return null;
  return `${digitos}@s.whatsapp.net`;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
    browser: ["Prospeccao-Fintech", "Chrome", "1.0"],
  });

  sock.ev.on("creds.update", saveCreds);

  return new Promise((resolve, reject) => {
    sock.ev.on("connection.update", async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        console.log("\nEscaneie o QR code abaixo com o WhatsApp (Aparelhos conectados > Conectar aparelho):\n");
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
        resolve(sock);
      }

      if (connection === "close") {
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        const deveReconectar = statusCode !== DisconnectReason.loggedOut;
        if (statusCode === DisconnectReason.loggedOut) {
          reject(new Error("Sessão desconectada (logout). Apague ./auth_info/ e escaneie o QR code de novo."));
        } else if (deveReconectar) {
          console.log("Conexão caiu, tentando reconectar...");
          try {
            const novoSock = await conectar();
            resolve(novoSock);
          } catch (err) {
            reject(err);
          }
        } else {
          reject(new Error("Conexão encerrada sem possibilidade de reconexão."));
        }
      }
    });
  });
}

async function checaNumero(sock, jid) {
  try {
    const resultado = await sock.onWhatsApp(jid);
    if (Array.isArray(resultado) && resultado.length > 0 && resultado[0].exists) {
      return { tem_whatsapp: "sim", whatsapp_jid: resultado[0].jid || "", whatsapp_erro: "" };
    }
    return { tem_whatsapp: "nao", whatsapp_jid: "", whatsapp_erro: "" };
  } catch (err) {
    return { tem_whatsapp: "erro", whatsapp_jid: "", whatsapp_erro: String(err.message || err) };
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  const entradaTexto = fs.readFileSync(args.entrada, "utf8");
  let linhas = parse(entradaTexto, { columns: true, skip_empty_lines: true });

  if (!linhas.length) {
    console.error("CSV de entrada vazio.");
    process.exit(1);
  }
  if (!(args.colunaTelefone in linhas[0])) {
    console.error(`Coluna '${args.colunaTelefone}' não encontrada no CSV de entrada.`);
    process.exit(1);
  }
  if (args.limite) linhas = linhas.slice(0, args.limite);

  console.log(`Conectando ao WhatsApp (primeira vez precisa escanear QR code)...`);
  const sock = await conectar();

  const resultados = [];
  const contagem = { sim: 0, nao: 0, erro: 0, sem_numero: 0 };

  for (let i = 0; i < linhas.length; i++) {
    const linha = linhas[i];
    const numeroBruto = linha[args.colunaTelefone];
    const jid = normalizaParaJid(numeroBruto);

    if (!jid) {
      resultados.push({ ...linha, tem_whatsapp: "sem_numero", whatsapp_jid: "", whatsapp_erro: "" });
      contagem.sem_numero++;
      continue;
    }

    process.stderr.write(`[${i + 1}/${linhas.length}] checando ${numeroBruto}... `);
    const r = await checaNumero(sock, jid);
    process.stderr.write(`${r.tem_whatsapp}\n`);
    contagem[r.tem_whatsapp] = (contagem[r.tem_whatsapp] || 0) + 1;
    resultados.push({ ...linha, ...r });

    // grava incrementalmente — se cair no meio, não perde o que já foi feito
    fs.writeFileSync(args.saida, stringify(resultados, { header: true }));

    if (i < linhas.length - 1) {
      const jitter = Math.floor(Math.random() * 1000);
      await sleep(args.delayMs + jitter);
    }
  }

  console.log("\nResumo:");
  console.log(contagem);
  console.log(`Gravado em ${args.saida}`);

  sock.end();
  process.exit(0);
}

main().catch((err) => {
  console.error("Erro fatal:", err);
  process.exit(1);
});
