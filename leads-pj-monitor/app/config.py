"""Configuração do sistema.

Duas camadas:
- Variáveis de ambiente (.env): só o essencial pra subir o container (banco,
  senha de login, chave de criptografia). Não muda no dia a dia.
- config_kv (banco): tudo que o usuário ajusta pela tela de Configurações —
  tribunais, classes/assuntos, pesos do score, provedor de enriquecimento,
  tetos de gasto, horário de execução. Chaves de API de provedores ficam
  criptografadas com Fernet usando APP_SECRET_KEY.
"""
import json
import os

from cryptography.fernet import Fernet, InvalidToken

from app.db import execute, fetch_one

PADRAO_CONFIG = {
    "tribunais_ativos": ["api_publica_tjsp"],
    "classes_tpu": [
        {"codigo": 1116, "nome": "Execução Fiscal"},
        {"codigo": 159, "nome": "Cumprimento de Sentença"},
        {"codigo": 156, "nome": "Execução de Título Extrajudicial"},
    ],
    "assuntos_tpu": [],
    "dias_retroativos_busca": 7,
    "limite_atraso_alerta_dias": 5,
    "ritmo_requisicao_segundos": 2.0,
    "filtro_somente_pj": True,
    # Exige natureza jurídica na faixa 2000-2999 ("Entidades Empresariais"
    # na classificação da Receita) — descoberto rodando uma leva real que,
    # sem isso, câmara de vereadores (1xxx, Administração Pública),
    # condomínio, igreja e associação (3xxx, Entidades sem Fins Lucrativos)
    # passavam como "ATIVA" e viravam lead, o que não faz sentido pra
    # prospecção comercial.
    "exigir_natureza_empresarial": True,
    "filtro_excluir_empresas_grandes": True,
    "teto_capital_social": 10_000_000,
    "naturezas_juridicas_excluidas": ["2046", "2054"],
    "limite_filiais_ativas": 10,
    "filtro_somente_me_epp": False,
    "filtro_situacao_ativa": True,
    "valor_causa_minimo": 0,
    "divida_pgfn_minima": 0,
    "pesos_score": {
        "valor_causa": 0.30,
        "divida_pgfn": 0.25,
        "porte": 0.15,
        "capital_social": 0.10,
        "numero_execucoes_recentes": 0.20,
    },
    "limiar_similaridade_fuzzy": 85,
    "provedor_enriquecimento_ativo": "fontedata",
    "teto_gasto_diario": 50.0,
    "teto_gasto_mensal": 800.0,
    "score_minimo_para_enriquecer": 50,
    "dias_cache_enriquecimento": 30,
    "dias_retencao_contato_nao_usado": 90,
    "horario_execucao_diaria": "06:00",
}


def _get_fernet() -> Fernet | None:
    chave = os.environ.get("APP_SECRET_KEY", "").strip()
    if not chave:
        return None
    return Fernet(chave.encode())


def cifrar(valor: str) -> str:
    f = _get_fernet()
    if f is None or not valor:
        return valor
    return f.encrypt(valor.encode()).decode()


def decifrar(valor: str) -> str:
    f = _get_fernet()
    if f is None or not valor:
        return valor
    try:
        return f.decrypt(valor.encode()).decode()
    except InvalidToken:
        return ""


def get_config(chave: str, default=None):
    row = fetch_one("SELECT valor_json FROM config_kv WHERE chave = :chave", {"chave": chave})
    if row:
        return row["valor_json"]
    if default is not None:
        return default
    return PADRAO_CONFIG.get(chave)


def set_config(chave: str, valor) -> None:
    execute(
        """
        INSERT INTO config_kv (chave, valor_json, atualizado_em)
        VALUES (:chave, CAST(:valor AS JSONB), now())
        ON CONFLICT (chave) DO UPDATE
        SET valor_json = CAST(:valor AS JSONB), atualizado_em = now()
        """,
        {"chave": chave, "valor": json.dumps(valor)},
    )


def get_all_config() -> dict:
    """Retorna a config efetiva: padrão + o que já foi salvo no banco."""
    efetivo = dict(PADRAO_CONFIG)
    from app.db import query_df

    df = query_df("SELECT chave, valor_json FROM config_kv")
    for _, row in df.iterrows():
        efetivo[row["chave"]] = row["valor_json"]
    return efetivo
