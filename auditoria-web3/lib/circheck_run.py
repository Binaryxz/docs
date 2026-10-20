#!/usr/bin/env python3
"""Roda o Circheck com recursão e pilha maiores.

    circheck_run.py <arquivo.circom> [args do circheck...]

O parser do Circheck é recursivo e estoura o limite padrão do Python (1000) nas constantes do
Poseidon do circomlib ("maximum recursion depth exceeded"). Aqui ele roda numa thread com
pilha de 512 MiB e limite de recursão alto.
"""

import sys
import threading


def main():
    from circheck.cli import main as circheck
    sys.argv = ["circheck", *sys.argv[1:]]
    circheck()


if __name__ == "__main__":
    sys.setrecursionlimit(1_000_000)
    threading.stack_size(512 * 1024 * 1024)
    t = threading.Thread(target=main)
    t.start()
    t.join()
