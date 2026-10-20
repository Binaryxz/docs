#!/usr/bin/env python3
"""Acha os harnesses de fuzzing (contratos "folha") de um projeto para o Medusa.

    medusa_alvos.py <raiz>

Um harness costuma ser montado por herança: as propriedades (property_*, echidna_*) ficam
em contratos-pai, e o contrato implantável é o que herda todos eles (FuzzTest is
PropertiesParent is ...). Imprime, separados por vírgula, os contratos que definem ou
herdam (direta ou indiretamente) uma propriedade e que nenhum outro contrato herda.
"""

import re
import sys
from pathlib import Path

IGNORAR = {"node_modules", "lib", "out", "forge-out", "cache"}
CONTRATO = re.compile(r"^\s*(abstract\s+)?contract\s+([A-Za-z0-9_]+)(\s+is\s+([^{]+))?\{", re.M)
PROPRIEDADE = re.compile(r"function\s+(property_|echidna_)")


def main(raiz: Path):
    pais, com_prop, abstratos = {}, set(), set()
    for f in raiz.rglob("*.sol"):
        if IGNORAR & set(f.relative_to(raiz).parts):
            continue
        texto = f.read_text(errors="ignore")
        achados = list(CONTRATO.finditer(texto))
        for i, m in enumerate(achados):
            nome = m.group(2)
            corpo = texto[m.end(): achados[i + 1].start() if i + 1 < len(achados) else len(texto)]
            herda = [re.sub(r"\(.*", "", p).strip() for p in (m.group(4) or "").split(",")]
            pais[nome] = [p for p in herda if p]
            if m.group(1):
                abstratos.add(nome)
            if PROPRIEDADE.search(corpo):
                com_prop.add(nome)

    def tem_prop(c, vistos=()):
        if c in com_prop:
            return True
        return any(tem_prop(p, vistos + (c,)) for p in pais.get(c, []) if p not in vistos)

    herdados = {p for ps in pais.values() for p in ps}
    folhas = sorted(c for c in pais if c not in herdados and c not in abstratos and tem_prop(c))
    print(",".join(folhas))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
