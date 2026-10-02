"""Aplica os arquivos .sql em migrations/, em ordem, uma única vez cada
(controlado por uma tabela própria). Roda automaticamente antes do
Streamlit subir, via docker-compose.yml — o usuário nunca precisa tocar
nisso manualmente.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import get_engine

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS _migracoes_aplicadas (
                    arquivo VARCHAR(200) PRIMARY KEY,
                    aplicado_em TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        aplicadas = {
            row[0]
            for row in conn.execute(text("SELECT arquivo FROM _migracoes_aplicadas"))
        }

    for arquivo in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if arquivo.name in aplicadas:
            continue
        print(f"Aplicando migração: {arquivo.name}")
        sql = arquivo.read_text(encoding="utf-8")
        with engine.begin() as conn:
            conn.execute(text(sql))
            conn.execute(
                text("INSERT INTO _migracoes_aplicadas (arquivo) VALUES (:arquivo)"),
                {"arquivo": arquivo.name},
            )
    print("Migrações em dia.")


if __name__ == "__main__":
    main()
