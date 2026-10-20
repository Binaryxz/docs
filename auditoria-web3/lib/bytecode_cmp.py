#!/usr/bin/env python3
"""Compara o bytecode on-chain com o que o próprio repositório compila.

    bytecode_cmp.py <estado.json> <saida.json> <dir_artefatos> [<dir_artefatos> ...]

Lê o código implantado de `estado.json` (onchain.py, campo `code` de cada contrato; o rótulo
é o nome do contrato) e procura o artefato Foundry `<Contrato>.json` nos diretórios dados.
Zera as posições de imutáveis (immutableReferences) e de bibliotecas linkadas nos dois lados,
corta o CBOR de metadados do fim de cada um (o hash de metadados muda com caminhos e
comentários, não com o código) e compara o resto byte a byte.
"""

import json
import re
import sys
from pathlib import Path


def sem_metadados(code: bytes) -> bytes:
    # Os 2 últimos bytes dão o tamanho do CBOR que os antecede (solc >= 0.4.7).
    if len(code) < 2:
        return code
    n = int.from_bytes(code[-2:], "big")
    if 0 < n + 2 <= len(code) and code[-n - 2] in (0xA1, 0xA2, 0xA3):
        return code[: -n - 2]
    return code


def mascarar(code: bytearray, refs: dict):
    for lista in refs.values():
        for r in lista:
            code[r["start"]: r["start"] + r["length"]] = bytes(r["length"])


def artefatos(dirs):
    achados = {}
    for d in dirs:
        for f in Path(d).rglob("*.json"):
            if f.parent.name.endswith(".sol"):
                achados.setdefault(f.stem, []).append(f)
    return achados


def main(estado, saida, dirs):
    est = json.load(open(estado))
    arts = artefatos(dirs)
    out = []
    for c in est["contratos"]:
        nome, code_hex = c.get("rotulo"), c.get("code")
        r = {"endereco": c["endereco"], "contrato": nome}
        if not code_hex or not nome:
            r["resultado"] = "sem código ou sem rótulo"
            out.append(r)
            continue
        cands = arts.get(nome, [])
        if not cands:
            r["resultado"] = "artefato não encontrado"
            out.append(r)
            continue
        onchain = bytes.fromhex(code_hex[2:] if code_hex.startswith("0x") else code_hex)
        r["resultado"] = "diferente"
        for f in cands:
            a = json.load(open(f)).get("deployedBytecode", {})
            obj = a.get("object", "")
            # Biblioteca não linkada: os 20 bytes do placeholder viram zero (e são mascarados abaixo).
            obj = re.sub(r"__\$[0-9a-fA-F]{34}\$__", "0" * 40, obj)
            try:
                build = bytearray(bytes.fromhex(obj[2:] if obj.startswith("0x") else obj))
            except ValueError:
                continue
            chain = bytearray(onchain)
            if len(build) != len(chain):
                r.setdefault("detalhe", []).append(f"{f}: {len(build)} bytes no build, {len(chain)} on-chain")
                continue
            refs = dict(a.get("immutableReferences") or {})
            for lib in (a.get("linkReferences") or {}).values():
                for lst in lib.values():
                    refs[f"lib{len(refs)}"] = lst
            mascarar(build, refs)
            mascarar(chain, refs)
            if sem_metadados(bytes(build)) == sem_metadados(bytes(chain)):
                n = sum(x["length"] for l in refs.values() for x in l)
                r["resultado"] = "idêntico" if n == 0 else f"idêntico fora de {n} bytes de imutáveis/bibliotecas"
                r["artefato"] = str(f)
                r.pop("detalhe", None)
                break
            r.setdefault("detalhe", []).append(f"{f}: mesmo tamanho, conteúdo diferente")
        out.append(r)
    json.dump(out, open(saida, "w"), indent=1, ensure_ascii=False)
    iguais = sum(1 for r in out if r["resultado"].startswith("idêntico"))
    print(f"{iguais}/{len(out)} idênticos ao build do repositório")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
