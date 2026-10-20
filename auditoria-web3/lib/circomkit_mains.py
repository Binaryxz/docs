#!/usr/bin/env python3
"""Gera os `component main` de projetos circomkit (circuits.json) para Picus e circom --inspect.

    circomkit_mains.py <repo_dir> <dir_saida>

Imprime o caminho absoluto de cada arquivo gerado, um por linha. Cada entrada de circuits.json
vira `include "<fonte>"; component main {public [...]} = Template(params);`, com o diretório de
circuitos do circomkit.json ao lado (padrão: ./circuits).
"""

import json
import re
import sys
from pathlib import Path


def main(repo: Path, saida: Path):
    for cfg in repo.rglob("circuits.json"):
        if "node_modules" in cfg.parts:
            continue
        try:
            circuitos = json.loads(cfg.read_text())
        except ValueError:
            continue
        if not isinstance(circuitos, dict):
            continue
        base = cfg.parent
        dir_circuitos = "circuits"
        kit = base / "circomkit.json"
        if kit.exists():
            try:
                dir_circuitos = json.loads(kit.read_text()).get("dirCircuits", "./circuits")
            except ValueError:
                pass
        for nome, c in circuitos.items():
            if not isinstance(c, dict) or "template" not in c or "file" not in c:
                continue
            fonte = (base / dir_circuitos / f"{c['file']}.circom").resolve()
            if not fonte.is_file() or repo.resolve() not in fonte.parents:
                print(f"circomkit: fonte ausente para {nome}: {fonte}", file=sys.stderr)
                continue
            ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
            pubs = c.get("pubs", [])
            if not ident.match(str(c["template"])) or not all(ident.match(str(x)) for x in pubs):
                print(f"circomkit: nome inválido em {nome}, ignorado", file=sys.stderr)
                continue
            try:
                params = ", ".join(str(int(p)) for p in c.get("params", []))
            except (TypeError, ValueError):
                print(f"circomkit: params inválidos em {nome}, ignorado", file=sys.stderr)
                continue
            publico = f" {{public [{', '.join(pubs)}]}}" if pubs else ""
            saida.mkdir(parents=True, exist_ok=True)
            destino = saida / f"{base.name}_{re.sub(r'[^A-Za-z0-9_]', '_', nome)}.circom"
            destino.write_text(
                f'pragma circom 2.0.0;\n\ninclude "{fonte}";\n\n'
                f"component main{publico} = {c['template']}({params});\n"
            )
            print(destino)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
