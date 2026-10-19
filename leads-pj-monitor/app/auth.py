"""Login por senha, simples de propósito — sistema de usuário único (o
advogado/escritório), não multi-usuário. A senha inicial vem do .env
(APP_LOGIN_PASSWORD); pode ser trocada depois pela tela de Configurações,
e nesse caso o hash passa a valer (tem prioridade sobre o .env).
"""
import hashlib
import os
import secrets

import streamlit as st

from app.db import execute, fetch_one


def _hash_senha(senha: str, sal: str) -> str:
    return hashlib.sha256((sal + senha).encode()).hexdigest()


def _senha_hash_salva() -> dict | None:
    return fetch_one("SELECT valor_json FROM config_kv WHERE chave = 'senha_login_hash'")


def trocar_senha(nova_senha: str) -> None:
    sal = secrets.token_hex(16)
    execute(
        """
        INSERT INTO config_kv (chave, valor_json, atualizado_em)
        VALUES ('senha_login_hash', CAST(:valor AS JSONB), now())
        ON CONFLICT (chave) DO UPDATE SET valor_json = CAST(:valor AS JSONB), atualizado_em = now()
        """,
        {"valor": f'{{"sal": "{sal}", "hash": "{_hash_senha(nova_senha, sal)}"}}'},
    )


def _senha_confere(tentativa: str) -> bool:
    salva = _senha_hash_salva()
    if salva:
        dados = salva["valor_json"]
        return _hash_senha(tentativa, dados["sal"]) == dados["hash"]
    senha_env = os.environ.get("APP_LOGIN_PASSWORD", "")
    return bool(senha_env) and secrets.compare_digest(tentativa, senha_env)


def exigir_login() -> None:
    """Chamar no topo de cada página. Interrompe a execução (st.stop) se
    o usuário ainda não autenticou nesta sessão de navegador."""
    if st.session_state.get("autenticado"):
        return

    st.title("🔒 Acesso ao sistema")
    with st.form("login"):
        senha = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar")
    if entrar:
        if _senha_confere(senha):
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.stop()
