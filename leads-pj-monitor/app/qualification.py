"""Módulo 3 (parte 3) — Qualificação dos leads já casados com CNPJ.

Dois passos sobre os leads com status 'novo' (ou 'revisao_manual' já
aprovados manualmente):

1. Filtros de exclusão (configuráveis em app.config): situação cadastral,
   somente PJ, exclusão de empresa grande, valor mínimo da causa, dívida
   PGFN mínima. Lead que cai em qualquer exclusão vira 'descartado' com o
   motivo registrado (aparece na tela de Leads pra auditoria).

2. Lead score 0-100: cada componente (valor da causa, dívida PGFN, porte,
   capital social, nº de execuções recentes do mesmo CNPJ) é normalizado
   por percentil dentro do lote sendo qualificado agora — isso evita ter
   que adivinhar um teto "razoável" pra cada métrica (que mudaria com o
   tempo) e se ajusta automaticamente ao perfil de leads que o sistema
   está encontrando. Convenção adotada (ajustável só trocando os pesos pra
   0 na tela de Configurações, já que o "melhor" lead depende do produto
   de cada escritório): quanto maior cada métrica, maior o score — mais
   causas, mais dívida, maior porte/capital e mais execuções recentes
   indicam uma empresa mais relevante para cobrança/negociação.
"""
import pandas as pd

from app.config import get_config
from app.db import execute, query_df

PORTE_ORDINAL = {"00": 0, "01": 1, "03": 2, "05": 3}  # não informado < ME < EPP < demais


def _empresa_e_marca_grande(cnpj: str, razao_social: str) -> bool:
    row = query_df(
        "SELECT termo, tipo FROM marcas_grandes_exclusao"
    )
    razao_upper = (razao_social or "").upper()
    cnpj_basico = cnpj[:8] if cnpj else ""
    for _, r in row.iterrows():
        if r["tipo"] == "cnpj" and r["termo"] == cnpj:
            return True
        if r["tipo"] == "cnpj_basico" and r["termo"] == cnpj_basico:
            return True
        if r["tipo"] == "substring" and r["termo"] in razao_upper:
            return True
    return False


def _motivo_exclusao(empresa: dict, config: dict) -> str | None:
    if config.get("filtro_situacao_ativa") and empresa["situacao_cadastral"] != "ATIVA":
        return f"situação cadastral {empresa['situacao_cadastral']}, não ATIVA"

    if config.get("filtro_excluir_empresas_grandes"):
        if empresa["capital_social"] and float(empresa["capital_social"]) > config["teto_capital_social"]:
            return f"capital social acima do teto (R$ {empresa['capital_social']:,.2f})"
        if str(empresa["natureza_juridica_codigo"]) in config.get("naturezas_juridicas_excluidas", []):
            return f"natureza jurídica excluída ({empresa['natureza_juridica_codigo']})"
        if (empresa["total_filiais_ativas"] or 0) > config["limite_filiais_ativas"]:
            return f"{empresa['total_filiais_ativas']} filiais ativas, acima do limite"
        if _empresa_e_marca_grande(empresa["cnpj"], empresa["razao_social"]):
            return "empresa na lista de exclusão de marca grande"

    if config.get("filtro_somente_me_epp") and empresa["porte_codigo"] not in ("01", "03"):
        return "porte fora de ME/EPP"

    if (empresa["valor_causa"] or 0) < config.get("valor_causa_minimo", 0):
        return "valor da causa abaixo do mínimo configurado"

    if (empresa["divida_pgfn_total"] or 0) < config.get("divida_pgfn_minima", 0):
        return "dívida PGFN abaixo do mínimo configurado"

    return None


def _percentil(serie: pd.Series) -> pd.Series:
    if serie.max() == serie.min():
        return pd.Series(50.0, index=serie.index)
    return serie.rank(pct=True) * 100


def qualificar_leads_pendentes() -> dict:
    cfg = {
        "filtro_situacao_ativa": get_config("filtro_situacao_ativa"),
        "filtro_excluir_empresas_grandes": get_config("filtro_excluir_empresas_grandes"),
        "teto_capital_social": get_config("teto_capital_social"),
        "naturezas_juridicas_excluidas": get_config("naturezas_juridicas_excluidas"),
        "limite_filiais_ativas": get_config("limite_filiais_ativas"),
        "filtro_somente_me_epp": get_config("filtro_somente_me_epp"),
        "valor_causa_minimo": get_config("valor_causa_minimo"),
        "divida_pgfn_minima": get_config("divida_pgfn_minima"),
    }
    pesos = get_config("pesos_score")

    leads_df = query_df(
        """
        SELECT
            l.id AS lead_id, l.cnpj,
            er.razao_social, er.situacao_cadastral, er.capital_social,
            er.natureza_juridica_codigo, er.porte_codigo,
            COALESCE(fc.total_filiais_ativas, 0) AS total_filiais_ativas,
            pr.valor_causa,
            COALESCE(pgfn.divida_total, 0) AS divida_pgfn_total,
            exec_recentes.total AS numero_execucoes_recentes
        FROM leads l
        JOIN empresas_receita er ON er.cnpj = l.cnpj
        JOIN processos pr ON pr.numero_cnj = l.numero_cnj
        LEFT JOIN empresas_filiais_count fc ON fc.cnpj_basico = er.cnpj_basico
        LEFT JOIN (
            SELECT cnpj, SUM(valor_consolidado) AS divida_total
            FROM pgfn_divida_ativa GROUP BY cnpj
        ) pgfn ON pgfn.cnpj = l.cnpj
        LEFT JOIN (
            SELECT l2.cnpj, count(*) AS total
            FROM leads l2 GROUP BY l2.cnpj
        ) exec_recentes ON exec_recentes.cnpj = l.cnpj
        WHERE l.status IN ('novo', 'revisao_manual')
        """
    )

    if leads_df.empty:
        return {"qualificados": 0, "descartados": 0}

    leads_df["porte_ordinal"] = leads_df["porte_codigo"].map(PORTE_ORDINAL).fillna(0)

    score_componentes = pd.DataFrame(
        {
            "valor_causa": _percentil(leads_df["valor_causa"].fillna(0)),
            "divida_pgfn": _percentil(leads_df["divida_pgfn_total"].fillna(0)),
            "porte": _percentil(leads_df["porte_ordinal"]),
            "capital_social": _percentil(leads_df["capital_social"].fillna(0)),
            "numero_execucoes_recentes": _percentil(leads_df["numero_execucoes_recentes"].fillna(0)),
        }
    )
    leads_df["score_lead"] = sum(
        score_componentes[c] * pesos.get(c, 0) for c in score_componentes.columns
    )

    descartados = 0
    qualificados = 0
    for _, row in leads_df.iterrows():
        motivo = _motivo_exclusao(row, cfg)
        if motivo:
            execute(
                "UPDATE leads SET status = 'descartado', motivo_descarte = :motivo, "
                "score_lead = :score, atualizado_em = now() WHERE id = :id",
                {"motivo": motivo, "score": float(row["score_lead"]), "id": int(row["lead_id"])},
            )
            descartados += 1
        else:
            novo_status = "em_analise"
            execute(
                "UPDATE leads SET status = :status, score_lead = :score, atualizado_em = now() WHERE id = :id",
                {"status": novo_status, "score": float(row["score_lead"]), "id": int(row["lead_id"])},
            )
            qualificados += 1

    return {"qualificados": qualificados, "descartados": descartados}
