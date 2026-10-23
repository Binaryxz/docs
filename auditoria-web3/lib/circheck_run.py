#!/usr/bin/env python3
"""Roda o Circheck sobre circuitos que usam o circomlib.

    circheck_run.py <arquivo.circom> [args do circheck...]

O Circheck 0.3.0 (pip e GitHub) não analisa o Poseidon do circomlib por dois motivos, e os
próprios autores deixam o circomlib comentado nos testes deles:

1. **Recursão.** O parser é recursivo e estoura nas constantes do Poseidon. Os módulos chamam
   `sys.setrecursionlimit(5000)` no import, então o limite é travado num piso alto antes disso e
   a análise roda numa thread com pilha de 512 MiB.
2. **Componente usado antes da linha que o cria.** Em `PoseidonEx`, `mix[r-1].out[j]` aparece
   no loop antes de `mix[r] = Mix(t, M)`. A checagem de tipos lê de cima para baixo, o
   componente ainda não tem sinais e dá `KeyError: 'out'`. Aqui, nesse caso, o tipo vem da
   atribuição `componente = Template(...)` do mesmo template, como o circom faz. Nada mais na
   análise muda: os detectores do Circheck rodam como publicados.
"""

import os
import sys
import threading
from dataclasses import fields, is_dataclass

PISO = 1_000_000


def _caminhos():
    import circheck
    base = os.path.dirname(os.path.abspath(circheck.__file__))
    for sub in ["parser", "astgen", "typecheck", "utils", "cdggen", "detect", "target"]:
        p = os.path.join(base, sub)
        if p not in sys.path:
            sys.path.append(p)


def _template_atribuido(no, componente):
    """Nome do template em `componente[...] = Template(...)` dentro de `no`, se houver."""
    from AST import Call, Substitution
    if isinstance(no, Substitution) and no.var == componente and no.op == "=" and isinstance(no.rhe, Call):
        return no.rhe.id
    filhos = []
    if is_dataclass(no):
        filhos = [getattr(no, f.name) for f in fields(no)]
    elif isinstance(no, (list, tuple)):
        filhos = list(no)
    for f in filhos:
        if is_dataclass(f) or isinstance(f, (list, tuple)):
            achado = _template_atribuido(f, componente)
            if achado:
                return achado
    return None


def _corrigir_componentes_adiantados():
    from StaticCheck import TemplateCircom, TypeCheck

    visitar_template = TypeCheck.visitTemplate
    visitar_variavel = TypeCheck.visitVariable

    def visit_template(self, ast, param):
        self._template_atual = ast
        return visitar_template(self, ast, param)

    def visit_variable(self, ast, param):
        try:
            return visitar_variavel(self, ast, param)
        except KeyError:
            nome = _template_atribuido(getattr(self, "_template_atual", None), ast.name)
            alvo = self.list_template.get(nome) if nome else None
            simbolo = next((env[ast.name] for env in param if ast.name in env), None)
            if alvo is None or simbolo is None:
                raise
            tipo = simbolo.mtype
            while not isinstance(tipo, TemplateCircom) and hasattr(tipo, "eleType"):
                tipo = tipo.eleType
            if not isinstance(tipo, TemplateCircom) or tipo.signals:
                raise
            origem = alvo.mtype
            tipo.name, tipo.signals = origem.name, origem.signals
            tipo.signals_in, tipo.signals_out = origem.signals_in, origem.signals_out
            return visitar_variavel(self, ast, param)

    TypeCheck.visitTemplate = visit_template
    TypeCheck.visitVariable = visit_variable


def _expandir_atribuicao_de_array():
    """`a <== b` com `a` e `b` arrays inteiros vira `a[i] <== b[i]` para cada elemento.

    O circom aceita atribuir um array inteiro; o grafo do Circheck só tem nós por elemento
    (`indices[0]`...) e dá `KeyError: 'indices'`. A expansão é o que a atribuição significa.
    """
    import re
    from AST import ArrayAccess, Number, Substitution, Variable
    from CDGGeneration import CDGGeneration

    visitar = CDGGeneration.visitSubstitution

    def visit_substitution(self, ast, param):
        try:
            return visitar(self, ast, param)
        except KeyError as e:
            nome = str(e.args[0]) if e.args else ""
            elementos = sorted(n for n in param["node"] if n.startswith(nome + "[") and "." not in n[len(nome):])
            if not elementos or not isinstance(ast.rhe, Variable):
                raise
            for el in elementos:
                indices = [int(x) for x in re.findall(r"\[(\d+)\]", el[len(nome):])]
                acesso = [ArrayAccess(ast.locate, Number(ast.locate, i)) for i in indices]
                visitar(self, Substitution(ast.locate, ast.var, list(ast.access) + acesso, ast.op,
                                           Variable(ast.rhe.locate, ast.rhe.name, list(ast.rhe.access) + acesso)),
                        param)
            return None

    CDGGeneration.visitSubstitution = visit_substitution


def main():
    _caminhos()
    _corrigir_componentes_adiantados()
    _expandir_atribuicao_de_array()
    from circheck.cli import main as circheck
    sys.argv = ["circheck", *sys.argv[1:]]
    circheck()


if __name__ == "__main__":
    original = sys.setrecursionlimit
    original(PISO)
    # Ignora reduções feitas pelos módulos do Circheck (setrecursionlimit(5000) no import).
    sys.setrecursionlimit = lambda n: original(max(n, PISO))
    threading.stack_size(512 * 1024 * 1024)
    t = threading.Thread(target=main)
    t.start()
    t.join()
