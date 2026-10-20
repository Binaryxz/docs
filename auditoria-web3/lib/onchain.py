#!/usr/bin/env python3
"""Fotografa o estado on-chain de contratos implantados, só com leituras (eth_call / getStorageAt).

    onchain.py <rpc_url> <saida.json> <endereço>[=rótulo] ...

Para cada endereço: se há código (e o sha256 do runtime), proxies EIP-1967, configuração de
Safe (dono, limite, singleton, módulos, guard, fallback handler) e o valor de cada getter comum
que o contrato responder (governor, owner, setupComplete, REGISTRY...). Também os saldos de ETH
e dos tokens passados em TOKENS (variável de ambiente, endereços separados por vírgula).
Nada é assinado nem enviado: não precisa de chave.
"""

import hashlib
import json
import os
import sys

from web3 import Web3

GETTERS = [
    # governança e papéis
    "governor()", "owner()", "pendingGovernor()", "guardian()", "treasurer()", "keeper()",
    "setupComplete()", "GOVERNANCE_DELAY()", "finalized()", "paused()", "mintPaused()",
    # ligações entre contratos
    "REGISTRY()", "RESERVE()", "MOVVA()", "USDT()", "ENTRYPOINT()", "ASSET()", "TREASURY()",
    "VENUE()", "STABLE()", "REFERENCE()", "BREAKER()", "PREDECESSOR()", "DEPLOYER()",
    "fxOracle()", "usdtUsdFeed()", "hedgeTreasury()", "feeRecipient()", "yieldVault()",
    "bandOracle()", "fiatEscrow()", "offRampEscrow()", "rewards()", "treasury()", "registry()",
    # parâmetros
    "DISPUTE_TIMEOUT()", "RULING_DELAY()", "RECEIPT_MAX_AGE()", "HEDGE_QUEUE_DELAY()",
    "fxMaxAge()", "usdtUsdMaxAge()", "maxAge()", "closedMaxAge()", "maxConfBps()",
    "supplyCap()", "mintFeeBps()", "redeemFeeBps()", "lpRewardShareBps()", "threshold()",
    "witnessThreshold()", "MIN_BOND()", "UNBONDING_PERIOD()", "exposurePaused()",
    # tokens
    "name()", "symbol()", "decimals()", "totalSupply()",
    # reserva
    "totalUsdt()", "collateralRatioBps()", "minWithdrawCollateralRatioBps()",
    # Safe
    "getOwners()", "getThreshold()", "VERSION()", "nonce()",
    # Privacy Pools
    "SCOPE()", "currentRoot()", "currentTreeSize()", "dead()", "latestRoot()",
]
TIPOS = {"getOwners()": "address[]", "name()": "string", "symbol()": "string", "VERSION()": "string",
         "setupComplete()": "bool", "finalized()": "bool", "paused()": "bool", "mintPaused()": "bool",
         "exposurePaused()": "bool", "dead()": "bool", "decimals()": "uint8"}
ENDERECO = {g for g in GETTERS if g[0].isupper() and g not in (
    "GOVERNANCE_DELAY()", "DISPUTE_TIMEOUT()", "RULING_DELAY()", "RECEIPT_MAX_AGE()",
    "HEDGE_QUEUE_DELAY()", "MIN_BOND()", "UNBONDING_PERIOD()", "SCOPE()", "VERSION()")} | {
    "governor()", "owner()", "pendingGovernor()", "guardian()", "treasurer()", "keeper()",
    "fxOracle()", "usdtUsdFeed()", "hedgeTreasury()", "feeRecipient()", "yieldVault()",
    "bandOracle()", "fiatEscrow()", "offRampEscrow()", "rewards()", "treasury()", "registry()"}

SLOT_IMPL = int("360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc", 16)
SLOT_ADMIN = int("b53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103", 16)
SLOT_GUARD = int("4a204f620c8c5ccdca3fd54d003badd85ba500436a431f0cbda4f558c93c34c8", 16)
SLOT_FALLBACK = int("6c9a6c4a39284e37ed1cf53d337577d14212a4870fb976a4366c693b939918d5", 16)


def main(rpc, saida, alvos):
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    tokens = [Web3.to_checksum_address(t) for t in os.environ.get("TOKENS", "").split(",") if t]
    bloco = w3.eth.block_number
    out = {"rpc": rpc, "chainId": w3.eth.chain_id, "bloco": bloco, "contratos": []}
    for alvo in alvos:
        end, _, rotulo = alvo.partition("=")
        a = Web3.to_checksum_address(end)
        code = w3.eth.get_code(a, block_identifier=bloco)
        c = {"endereco": a, "rotulo": rotulo, "bytes": len(code),
             "runtime_sha256": hashlib.sha256(code).hexdigest() if code else None,
             "code": "0x" + code.hex() if code else None,
             "eth": str(w3.eth.get_balance(a, block_identifier=bloco)), "getters": {}, "saldos": {}}
        if code:
            for nome, slot in (("impl_eip1967", SLOT_IMPL), ("admin_eip1967", SLOT_ADMIN)):
                v = w3.eth.get_storage_at(a, slot, block_identifier=bloco)[-20:]
                if any(v):
                    c[nome] = Web3.to_checksum_address(v)
            for g in GETTERS:
                tipo = TIPOS.get(g, "address" if g in ENDERECO else "uint256")
                try:
                    r = w3.eth.call({"to": a, "data": Web3.keccak(text=g)[:4]}, bloco)
                    if not r:
                        continue
                    v = w3.codec.decode([tipo], r)[0]
                    c["getters"][g] = v if isinstance(v, (bool, str, list)) else str(v)
                except Exception:
                    pass
            if "getThreshold()" in c["getters"]:
                # Só num Safe o slot 0 é o singleton; em outro contrato é uma variável qualquer.
                for nome, slot in (("safe_singleton", 0), ("safe_guard", SLOT_GUARD),
                                   ("safe_fallback", SLOT_FALLBACK)):
                    v = w3.eth.get_storage_at(a, slot, block_identifier=bloco)[-20:]
                    if any(v):
                        c[nome] = Web3.to_checksum_address(v)
                try:
                    sel = Web3.keccak(text="getModulesPaginated(address,uint256)")[:4]
                    data = sel + w3.codec.encode(["address", "uint256"], ["0x0000000000000000000000000000000000000001", 10])
                    mods, _ = w3.codec.decode(["address[]", "address"], w3.eth.call({"to": a, "data": data}, bloco))
                    c["safe_modulos"] = list(mods)
                except Exception:
                    pass
        for t in tokens:
            try:
                data = Web3.keccak(text="balanceOf(address)")[:4] + w3.codec.encode(["address"], [a])
                c["saldos"][t] = str(w3.codec.decode(["uint256"], w3.eth.call({"to": t, "data": data}, bloco))[0])
            except Exception:
                pass
        out["contratos"].append(c)
    json.dump(out, open(saida, "w"), indent=1, ensure_ascii=False)
    print(f"{len(alvos)} endereço(s) lidos no bloco {bloco}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
