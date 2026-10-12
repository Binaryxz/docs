// Carteiras EVM e transferências de USDT/ETH na Ethereum.
//
// As chaves privadas nunca saem deste container: o control plane só pede
// endereço, saldo, assinatura de mensagem e envio de transferência.
// Dois tipos de carteira:
//   - "treasury": criadas pela plataforma, em /data/wallets/<id>.json
//   - "agent":    a carteira de cada agente Automaton (~/.automaton/wallet.json)

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { createRequire } from "node:module";

const AUTOMATON_DIR = process.env.AUTOMATON_DIR || "/opt/automaton";
const DATA_DIR = process.env.DATA_DIR || "/data";
const WALLETS_DIR = path.join(DATA_DIR, "wallets");
const AGENTS_DIR = path.join(DATA_DIR, "agents");

const NETWORK = (process.env.ETH_NETWORK || "mainnet").toLowerCase(); // mainnet | sepolia
const DEFAULT_RPC = {
  mainnet: "https://ethereum-rpc.publicnode.com",
  sepolia: "https://ethereum-sepolia-rpc.publicnode.com",
};
const RPC_URL = process.env.ETH_RPC_URL || DEFAULT_RPC[NETWORK] || DEFAULT_RPC.mainnet;
// USDT (Tether) oficial na Ethereum mainnet. Em testnet, informe o contrato do token de teste.
const USDT_CONTRACT = process.env.USDT_CONTRACT || (NETWORK === "mainnet" ? "0xdAC17F958D2ee523a2206206994597C13D831ec7" : "");
const MAX_USDT_PER_TX = Number(process.env.MAX_USDT_PER_TX || 100);
const MAX_ETH_PER_TX = Number(process.env.MAX_ETH_PER_TX || 0.05);

const req = createRequire(path.join(AUTOMATON_DIR, "package.json"));
const viem = req("viem");
const chains = req("viem/chains");
const { generatePrivateKey, privateKeyToAccount } = req("viem/accounts");

// O USDT da mainnet não retorna bool em transfer(); com o ABI ERC-20 padrão a
// simulação falharia ao decodificar o retorno vazio.
const USDT_TRANSFER_ABI = [{
  type: "function", name: "transfer", stateMutability: "nonpayable",
  inputs: [{ name: "to", type: "address" }, { name: "value", type: "uint256" }], outputs: [],
}];

const chain = NETWORK === "sepolia" ? chains.sepolia : chains.mainnet;
const transport = viem.http(RPC_URL, { timeout: 20_000 });
const publicClient = viem.createPublicClient({ chain, transport });

fs.mkdirSync(WALLETS_DIR, { recursive: true, mode: 0o700 });

export const networkInfo = () => ({
  network: NETWORK,
  chainId: chain.id,
  rpc: RPC_URL.replace(/\/\/([^/]+)@/, "//***@"),
  usdtContract: USDT_CONTRACT || null,
  limits: { usdtPerTx: MAX_USDT_PER_TX, ethPerTx: MAX_ETH_PER_TX },
});

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf-8"));
}

function treasuryFile(id) {
  if (!/^[a-z0-9-]{3,64}$/.test(id)) throw new Error("id de carteira inválido");
  return path.join(WALLETS_DIR, `${id}.json`);
}

/** Resolve "t:<id>" (tesouraria) ou "a:<agentId>" (agente) para {account, meta}. */
function load(walletId) {
  const [kind, id] = String(walletId).split(":");
  let data;
  let label;
  if (kind === "t") {
    const file = treasuryFile(id);
    if (!fs.existsSync(file)) throw new Error("carteira não encontrada");
    data = readJson(file);
    label = data.label;
  } else if (kind === "a") {
    if (!/^[a-z0-9-]{3,64}$/.test(id)) throw new Error("id de agente inválido");
    const file = path.join(AGENTS_DIR, id, ".automaton", "wallet.json");
    if (!fs.existsSync(file)) throw new Error("carteira do agente não encontrada");
    data = readJson(file);
    const meta = path.join(AGENTS_DIR, id, "meta.json");
    label = fs.existsSync(meta) ? `Agente ${readJson(meta).name}` : `Agente ${id}`;
  } else {
    throw new Error("id de carteira inválido");
  }
  if (!data.privateKey) throw new Error("carteira sem chave EVM");
  return { account: privateKeyToAccount(data.privateKey), kind: kind === "t" ? "treasury" : "agent", label, id: walletId };
}

export function listWallets() {
  const out = [];
  for (const f of fs.readdirSync(WALLETS_DIR).filter((f) => f.endsWith(".json"))) {
    try {
      const w = load(`t:${f.slice(0, -5)}`);
      out.push({ id: w.id, kind: w.kind, label: w.label, address: w.account.address });
    } catch {}
  }
  if (fs.existsSync(AGENTS_DIR)) {
    for (const id of fs.readdirSync(AGENTS_DIR)) {
      try {
        const w = load(`a:${id}`);
        out.push({ id: w.id, kind: w.kind, label: w.label, address: w.account.address });
      } catch {}
    }
  }
  return out;
}

export function createWallet(label) {
  const id = crypto.randomBytes(4).toString("hex");
  const privateKey = generatePrivateKey();
  const data = { label: String(label || "Tesouraria").slice(0, 60), privateKey, createdAt: new Date().toISOString() };
  fs.writeFileSync(treasuryFile(id), JSON.stringify(data, null, 2), { mode: 0o600 });
  const account = privateKeyToAccount(privateKey);
  return { id: `t:${id}`, kind: "treasury", label: data.label, address: account.address };
}

export async function balance(walletId) {
  const { account } = load(walletId);
  const [wei, usdt] = await Promise.all([
    publicClient.getBalance({ address: account.address }),
    USDT_CONTRACT
      ? publicClient.readContract({ address: USDT_CONTRACT, abi: viem.erc20Abi, functionName: "balanceOf", args: [account.address] })
      : Promise.resolve(null),
  ]);
  return {
    address: account.address,
    eth: viem.formatEther(wei),
    usdt: usdt === null ? null : viem.formatUnits(usdt, 6),
  };
}

export async function signMessage(walletId, message) {
  const { account } = load(walletId);
  const text = String(message || "");
  if (!text) throw new Error("mensagem vazia");
  return { address: account.address, message: text, signature: await account.signMessage({ message: text }) };
}

/** Assina e envia uma transferência. asset: "USDT" | "ETH". */
export async function transfer(walletId, { to, amount, asset = "USDT" }) {
  const { account } = load(walletId);
  if (!viem.isAddress(to)) throw new Error("endereço de destino inválido");
  const value = Number(amount);
  if (!Number.isFinite(value) || value <= 0) throw new Error("valor inválido");

  const wallet = viem.createWalletClient({ account, chain, transport });
  if (asset === "ETH") {
    if (value > MAX_ETH_PER_TX) throw new Error(`limite por transação: ${MAX_ETH_PER_TX} ETH`);
    const wei = viem.parseEther(String(amount));
    const have = await publicClient.getBalance({ address: account.address });
    if (have <= wei) throw new Error(`saldo insuficiente: ${viem.formatEther(have)} ETH disponível (o gás também sai daqui)`);
    const hash = await wallet.sendTransaction({ to, value: wei });
    return { hash, asset, amount: String(amount), from: account.address, to, network: NETWORK };
  }
  if (asset !== "USDT") throw new Error("ativo não suportado");
  if (!USDT_CONTRACT) throw new Error("defina USDT_CONTRACT para esta rede");
  if (value > MAX_USDT_PER_TX) throw new Error(`limite por transação: ${MAX_USDT_PER_TX} USDT`);
  const units = viem.parseUnits(String(amount), 6);
  const [tokenBal, gasBal] = await Promise.all([
    publicClient.readContract({ address: USDT_CONTRACT, abi: viem.erc20Abi, functionName: "balanceOf", args: [account.address] }),
    publicClient.getBalance({ address: account.address }),
  ]);
  if (tokenBal < units) throw new Error(`saldo insuficiente: ${viem.formatUnits(tokenBal, 6)} USDT disponível`);
  if (gasBal === 0n) throw new Error("a carteira não tem ETH para pagar o gás da transação");
  // Simula antes de assinar: falha aqui (saldo, gás) não gasta nada.
  const { request } = await publicClient.simulateContract({
    account,
    address: USDT_CONTRACT,
    abi: USDT_TRANSFER_ABI,
    functionName: "transfer",
    args: [to, units],
  });
  const hash = await wallet.writeContract(request);
  return { hash, asset, amount: String(amount), from: account.address, to, network: NETWORK };
}

export async function txStatus(hash) {
  const receipt = await publicClient.getTransactionReceipt({ hash }).catch(() => null);
  if (!receipt) return { hash, status: "pending" };
  return { hash, status: receipt.status === "success" ? "confirmed" : "failed", block: Number(receipt.blockNumber) };
}
