"""Conexão com o banco e helpers de leitura/escrita.

Usa SQLAlchemy Core (não ORM) de propósito: este sistema é essencialmente um
pipeline de dados com telas em cima, então SQL direto é mais simples de
auditar e manter do que um mapeamento objeto-relacional completo.
"""
import os

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = os.environ.get(
            "DATABASE_URL",
            "postgresql+psycopg2://leads_pj:leads_pj@localhost/leads_pj",
        )
        _engine = create_engine(url, pool_pre_ping=True, future=True)
    return _engine


def query_df(sql: str, params: dict | None = None) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def execute(sql: str, params: dict | None = None) -> None:
    with get_engine().begin() as conn:
        conn.execute(text(sql), params or {})


def execute_many(sql: str, params_list: list[dict]) -> None:
    if not params_list:
        return
    with get_engine().begin() as conn:
        conn.execute(text(sql), params_list)


def fetch_one(sql: str, params: dict | None = None):
    with get_engine().connect() as conn:
        result = conn.execute(text(sql), params or {})
        row = result.fetchone()
        return dict(row._mapping) if row else None
