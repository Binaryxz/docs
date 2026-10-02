"""Busca de classes/assuntos da Tabela Processual Unificada (TPU) do CNJ.

A consulta pública oficial da TPU (cnj.jus.br/sgt) é uma página antiga baseada
em AJAX legado (sajax), sem uma API REST estável pra consultar programaticamente
— tentar raspar aquilo é frágil e quebra sem aviso. Em vez disso:

1. Uma pequena lista inicial (abaixo) com os códigos mais usados neste tipo de
   prospecção (execução fiscal e afins) pra já funcionar na primeira instalação.
2. A busca na tela de Leads/Configurações também inclui qualquer classe/assunto
   que o próprio DataJud já trouxe nos processos coletados — como o DataJud
   retorna classe.codigo/classe.nome e assuntos.codigo/assuntos.nome direto da
   TPU, isso cresce organicamente e é sempre fiel à fonte oficial, sem depender
   de raspar a página antiga do CNJ.

Se quiser conferir um código específico manualmente, a consulta pública fica em
https://www.cnj.jus.br/sgt/consulta_publica_classes.php (e _assuntos.php).
"""
from app.db import query_df

CLASSES_SEED = [
    {"codigo": 1116, "nome": "Execução Fiscal"},
    {"codigo": 156, "nome": "Execução de Título Extrajudicial"},
    {"codigo": 159, "nome": "Cumprimento de Sentença"},
    {"codigo": 155, "nome": "Monitória"},
    {"codigo": 7, "nome": "Procedimento Comum Cível"},
]

ASSUNTOS_SEED = [
    {"codigo": 10433, "nome": "Cobrança"},
    {"codigo": 7771, "nome": "Dívida Ativa"},
]


def buscar_classes(termo: str = "") -> list[dict]:
    vistos = {c["codigo"]: c for c in CLASSES_SEED}
    try:
        df = query_df(
            "SELECT DISTINCT classe_codigo AS codigo, classe_nome AS nome "
            "FROM processos WHERE classe_codigo IS NOT NULL"
        )
        for _, row in df.iterrows():
            vistos.setdefault(int(row["codigo"]), {"codigo": int(row["codigo"]), "nome": row["nome"]})
    except Exception:
        pass  # banco pode ainda não existir na primeira execução; segue só com o seed

    resultado = list(vistos.values())
    if termo:
        termo_lower = termo.lower()
        resultado = [c for c in resultado if termo_lower in (c["nome"] or "").lower()]
    return sorted(resultado, key=lambda c: c["nome"] or "")


def buscar_assuntos(termo: str = "") -> list[dict]:
    vistos = {a["codigo"]: a for a in ASSUNTOS_SEED}
    try:
        df = query_df(
            "SELECT DISTINCT assunto_codigo AS codigo, assunto_nome AS nome "
            "FROM processos WHERE assunto_codigo IS NOT NULL"
        )
        for _, row in df.iterrows():
            vistos.setdefault(int(row["codigo"]), {"codigo": int(row["codigo"]), "nome": row["nome"]})
    except Exception:
        pass

    resultado = list(vistos.values())
    if termo:
        termo_lower = termo.lower()
        resultado = [a for a in resultado if termo_lower in (a["nome"] or "").lower()]
    return sorted(resultado, key=lambda a: a["nome"] or "")
