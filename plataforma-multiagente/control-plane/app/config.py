"""Configuração do control plane, toda via variáveis de ambiente."""

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # LLM: qualquer endpoint OpenAI-compatível (Ollama, vLLM, OmniRoute, ...).
    llm_base_url: str = _env("LLM_BASE_URL", "http://ollama:11434/v1").rstrip("/")
    llm_api_key: str = _env("LLM_API_KEY", "local")
    llm_model: str = _env("LLM_MODEL", "olmo-3.1:32b-instruct")
    llm_timeout_s: float = float(_env("LLM_TIMEOUT_S", "600"))

    # Runner do Conway Automaton.
    runner_url: str = _env("RUNNER_URL", "http://automaton-runner:7000").rstrip("/")
    runner_token: str = _env("RUNNER_TOKEN", "")

    # Plataforma.
    db_path: str = _env("DB_PATH", "/data/platform.db")
    admin_token: str = _env("ADMIN_TOKEN", "")  # token Bearer para automações (n8n)
    admin_user: str = _env("ADMIN_USER", "admin")
    admin_password: str = _env("ADMIN_PASSWORD", "")  # vazio = sem login (só em 127.0.0.1)
    session_secret: str = _env("SESSION_SECRET", "")
    cookie_secure: bool = _env("COOKIE_SECURE", "false").lower() == "true"
    run_timeout_s: int = int(_env("RUN_TIMEOUT_S", "1800"))
    run_webhook_url: str = _env("RUN_WEBHOOK_URL", "")  # ex.: webhook do n8n


settings = Settings()
