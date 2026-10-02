"""Teste de integração do matching + qualificação — precisa de um Postgres
de verdade (usa as mesmas tabelas da aplicação). Roda automaticamente dentro
do container (docker compose exec app pytest), ou localmente apontando
DATABASE_URL pra um Postgres de teste. Pulado se DATABASE_URL não estiver
definida, pra não quebrar um "pytest" rodado sem banco por engano.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="precisa de DATABASE_URL apontando pra um Postgres de teste"
)


@pytest.fixture(autouse=True)
def _limpar_tabelas():
    from app.db import execute

    execute(
        "TRUNCATE processos, partes, leads, empresas_receita, empresas_filiais_count, "
        "socios, pgfn_divida_ativa, marcas_grandes_exclusao CASCADE"
    )
    yield
    execute(
        "TRUNCATE processos, partes, leads, empresas_receita, empresas_filiais_count, "
        "socios, pgfn_divida_ativa, marcas_grandes_exclusao CASCADE"
    )


def _inserir_processo_com_lead(numero_cnj: str, cnpj: str, situacao: str = "ATIVA", capital=None):
    from app.db import execute

    execute(
        "INSERT INTO processos (numero_cnj, tribunal, uf, data_ajuizamento, valor_causa) "
        "VALUES (:n, 'api_publica_tjsp', 'SP', now(), 10000)",
        {"n": numero_cnj},
    )
    execute(
        "INSERT INTO empresas_receita "
        "(cnpj, cnpj_basico, razao_social, situacao_cadastral, capital_social, porte_codigo, natureza_juridica_codigo) "
        "VALUES (:c, :b, 'EMPRESA TESTE', :s, :cap, '03', 2062)",  # 2062 = Sociedade Empresária Limitada
        {"c": cnpj, "b": cnpj[:8], "s": situacao, "cap": capital},
    )
    execute(
        "INSERT INTO leads (numero_cnj, cnpj, metodo_match, score_match, status) "
        "VALUES (:n, :c, 'cnpj_exato', 100, 'novo')",
        {"n": numero_cnj, "c": cnpj},
    )


def test_qualificacao_descarta_empresa_inativa():
    from app.qualification import qualificar_leads_pendentes
    from app.db import fetch_one

    _inserir_processo_com_lead("1111111-11.2026.8.26.0100", "11111111000100", situacao="BAIXADA")
    resultado = qualificar_leads_pendentes()
    assert resultado["descartados"] == 1

    lead = fetch_one("SELECT status, motivo_descarte FROM leads WHERE numero_cnj = :n", {"n": "1111111-11.2026.8.26.0100"})
    assert lead["status"] == "descartado"
    assert "ATIVA" in lead["motivo_descarte"]


def test_qualificacao_descarta_capital_social_acima_do_teto():
    from app.config import set_config
    from app.qualification import qualificar_leads_pendentes
    from app.db import fetch_one

    set_config("teto_capital_social", 1_000_000)
    _inserir_processo_com_lead("2222222-22.2026.8.26.0100", "22222222000100", capital=5_000_000)
    resultado = qualificar_leads_pendentes()
    assert resultado["descartados"] == 1

    lead = fetch_one("SELECT motivo_descarte FROM leads WHERE numero_cnj = :n", {"n": "2222222-22.2026.8.26.0100"})
    assert "capital social" in lead["motivo_descarte"]


def test_qualificacao_descarta_natureza_juridica_nao_empresarial():
    """Regressão de um achado real: câmara de vereadores, condomínio e
    associação vinham ATIVA na Receita e passavam como lead antes deste
    filtro (naturezas 1xxx/3xxx, fora da faixa 2000-2999 de "Entidades
    Empresariais")."""
    from app.qualification import qualificar_leads_pendentes
    from app.db import execute, fetch_one

    _inserir_processo_com_lead("4444444-44.2026.8.26.0100", "44444444000100", capital=1_000)
    execute(
        "UPDATE empresas_receita SET natureza_juridica_codigo = 1066 WHERE cnpj = '44444444000100'"
    )
    resultado = qualificar_leads_pendentes()
    assert resultado["descartados"] == 1

    lead = fetch_one(
        "SELECT motivo_descarte FROM leads WHERE numero_cnj = :n", {"n": "4444444-44.2026.8.26.0100"}
    )
    assert "natureza jurídica" in lead["motivo_descarte"]


def test_qualificacao_aprova_empresa_dentro_dos_criterios():
    from app.qualification import qualificar_leads_pendentes
    from app.db import fetch_one

    _inserir_processo_com_lead("3333333-33.2026.8.26.0100", "33333333000100", capital=50_000)
    resultado = qualificar_leads_pendentes()
    assert resultado["qualificados"] == 1

    lead = fetch_one("SELECT status, score_lead FROM leads WHERE numero_cnj = :n", {"n": "3333333-33.2026.8.26.0100"})
    assert lead["status"] == "em_analise"
    assert lead["score_lead"] is not None
