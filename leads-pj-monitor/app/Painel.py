import streamlit as st

st.set_page_config(page_title="Leads PJ — Painel", page_icon="📋", layout="wide")

from app.auth import exigir_login  # noqa: E402

exigir_login()

import pandas as pd  # noqa: E402

from app.datajud_client import medir_atraso_por_tribunal  # noqa: E402
from app.db import query_df  # noqa: E402
from app.scheduler import execucao_esta_em_andamento, obter_agendador, rodar_pipeline_diario_em_thread  # noqa: E402
from app.config import get_config  # noqa: E402

obter_agendador()  # garante que o agendador esteja rodando desde a 1ª página aberta

st.title("📋 Painel")

col_botao, col_status = st.columns([1, 3])
with col_botao:
    if st.button("▶️ Rodar agora", type="primary", disabled=execucao_esta_em_andamento()):
        rodar_pipeline_diario_em_thread()
        st.success("Coleta disparada em segundo plano — atualize a página em alguns instantes.")
with col_status:
    if execucao_esta_em_andamento():
        st.info("⏳ Uma coleta já está em andamento.")

st.divider()

leads_hoje = query_df(
    "SELECT count(*) AS total FROM leads WHERE criado_em::date = current_date"
)["total"][0]
leads_semana = query_df(
    "SELECT count(*) AS total FROM leads WHERE criado_em >= current_date - interval '7 days'"
)["total"][0]
leads_em_analise = query_df("SELECT count(*) AS total FROM leads WHERE status = 'em_analise'")["total"][0]
leads_revisao = query_df("SELECT count(*) AS total FROM leads WHERE status = 'revisao_manual'")["total"][0]

gasto_mes = query_df(
    "SELECT COALESCE(SUM(custo), 0) AS total FROM gasto_enriquecimento "
    "WHERE criado_em >= date_trunc('month', current_date)"
)["total"][0]
teto_mensal = get_config("teto_gasto_mensal")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Leads novos hoje", int(leads_hoje))
c2.metric("Leads novos (7 dias)", int(leads_semana))
c3.metric("Prontos para enriquecer", int(leads_em_analise))
c4.metric("Na fila de revisão manual", int(leads_revisao))
c5.metric("Gasto no mês", f"R$ {gasto_mes:,.2f}", delta=f"teto: R$ {teto_mensal:,.2f}", delta_color="off")

st.divider()
st.subheader("Atraso de coleta por tribunal")
st.caption(
    "Tempo médio entre o ajuizamento do processo e a sua descoberta por este sistema. "
    f"Alerta configurado para acima de {get_config('limite_atraso_alerta_dias')} dias."
)

df_atraso = medir_atraso_por_tribunal()
if df_atraso.empty:
    st.info("Ainda não há processos coletados — rode a coleta pela primeira vez.")
else:
    limite = get_config("limite_atraso_alerta_dias")

    def _linha_com_alerta(row):
        if row["atraso_medio_dias"] and row["atraso_medio_dias"] > limite:
            return ["background-color: #ffe4e4"] * len(row)
        return [""] * len(row)

    st.dataframe(
        df_atraso.style.apply(_linha_com_alerta, axis=1),
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.subheader("Últimas execuções")
df_execucoes = query_df(
    """
    SELECT tipo, tribunal, status, processos_novos, iniciado_em, finalizado_em, erro_detalhe
    FROM execucoes_coleta
    ORDER BY iniciado_em DESC
    LIMIT 20
    """
)
if df_execucoes.empty:
    st.info("Nenhuma execução registrada ainda.")
else:
    st.dataframe(df_execucoes, use_container_width=True, hide_index=True)
