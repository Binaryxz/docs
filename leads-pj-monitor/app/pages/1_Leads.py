import json
from datetime import date, timedelta

import streamlit as st

st.set_page_config(page_title="Leads PJ — Leads", page_icon="📋", layout="wide")

from app.auth import exigir_login  # noqa: E402

exigir_login()

from app.db import execute, query_df  # noqa: E402
from app.tpu import buscar_classes  # noqa: E402

st.title("🔎 Leads")

# --- Nichos salvos ---
nichos_df = query_df("SELECT id, nome, filtros_json FROM nichos_salvos ORDER BY nome")
nomes_nichos = ["(nenhum)"] + nichos_df["nome"].tolist()
nicho_escolhido = st.selectbox("Carregar nicho salvo", nomes_nichos)

filtros_do_nicho = {}
if nicho_escolhido != "(nenhum)":
    filtros_do_nicho = nichos_df.loc[nichos_df["nome"] == nicho_escolhido, "filtros_json"].iloc[0]

with st.expander("Filtros", expanded=True):
    classes_disponiveis = buscar_classes()
    nomes_classes = [c["nome"] for c in classes_disponiveis]
    classes_default = [
        c["nome"] for c in classes_disponiveis if c["codigo"] in filtros_do_nicho.get("classes_codigos", [])
    ]
    classes_selecionadas_nomes = st.multiselect(
        "Categoria do processo (classe TPU)", nomes_classes, default=classes_default
    )
    classes_codigos = [
        c["codigo"] for c in classes_disponiveis if c["nome"] in classes_selecionadas_nomes
    ]

    col_atalho1, col_atalho2, col_atalho3, col_atalho4 = st.columns(4)
    atalho = None
    if col_atalho1.button("Hoje"):
        atalho = 0
    if col_atalho2.button("7 dias"):
        atalho = 7
    if col_atalho3.button("30 dias"):
        atalho = 30
    if col_atalho4.button("Limpar datas"):
        atalho = -1

    data_default_inicio = date.today() - timedelta(days=filtros_do_nicho.get("dias_atalho", 30))
    if atalho is not None and atalho >= 0:
        data_default_inicio = date.today() - timedelta(days=atalho)

    col_data1, col_data2 = st.columns(2)
    data_inicio = col_data1.date_input(
        "Ajuizamento de", value=None if atalho == -1 else data_default_inicio
    )
    data_fim = col_data2.date_input("até", value=None if atalho == -1 else date.today())

    col_f1, col_f2, col_f3 = st.columns(3)
    tribunal = col_f1.text_input("Tribunal (alias DataJud, opcional)", value=filtros_do_nicho.get("tribunal", ""))
    uf = col_f2.text_input("UF", value=filtros_do_nicho.get("uf", ""), max_chars=2)
    cnae = col_f3.text_input("CNAE principal (opcional)", value=filtros_do_nicho.get("cnae", ""))

    col_f4, col_f5, col_f6 = st.columns(3)
    valor_minimo = col_f4.number_input(
        "Valor da causa mínimo (R$)", min_value=0.0, value=float(filtros_do_nicho.get("valor_minimo", 0))
    )
    porte_opcoes = {"Qualquer": None, "Não informado": "00", "ME": "01", "EPP": "03", "Demais": "05"}
    porte_escolhido = col_f5.selectbox("Porte", list(porte_opcoes.keys()))
    score_minimo = col_f6.slider("Score mínimo", 0, 100, int(filtros_do_nicho.get("score_minimo", 0)))

    status_opcoes = ["novo", "em_analise", "revisao_manual", "descartado"]
    status_selecionados = st.multiselect(
        "Status", status_opcoes, default=filtros_do_nicho.get("status", ["em_analise"])
    )

    col_salvar1, col_salvar2 = st.columns([3, 1])
    nome_novo_nicho = col_salvar1.text_input("Nome para salvar este conjunto de filtros como nicho")
    if col_salvar2.button("💾 Salvar nicho") and nome_novo_nicho:
        filtros_atuais = {
            "classes_codigos": classes_codigos,
            "tribunal": tribunal,
            "uf": uf,
            "cnae": cnae,
            "valor_minimo": valor_minimo,
            "score_minimo": score_minimo,
            "status": status_selecionados,
            "dias_atalho": atalho if atalho and atalho >= 0 else 30,
        }
        execute(
            """
            INSERT INTO nichos_salvos (nome, filtros_json) VALUES (:nome, CAST(:filtros AS JSONB))
            ON CONFLICT (nome) DO UPDATE SET filtros_json = CAST(:filtros AS JSONB)
            """,
            {"nome": nome_novo_nicho, "filtros": json.dumps(filtros_atuais)},
        )
        st.success(f"Nicho '{nome_novo_nicho}' salvo.")
        st.rerun()

# --- Monta a query ---
condicoes = ["1=1"]
parametros = {}

if classes_codigos:
    condicoes.append("pr.classe_codigo = ANY(:classes)")
    parametros["classes"] = classes_codigos
if data_inicio:
    condicoes.append("pr.data_ajuizamento::date >= :data_inicio")
    parametros["data_inicio"] = data_inicio
if data_fim:
    # Comparar por ::date (não pelo timestamp cru): "até hoje" precisa
    # incluir processos ajuizados a qualquer hora de hoje, não só até
    # meia-noite.
    condicoes.append("pr.data_ajuizamento::date <= :data_fim")
    parametros["data_fim"] = data_fim
if tribunal:
    condicoes.append("pr.tribunal = :tribunal")
    parametros["tribunal"] = tribunal
if uf:
    condicoes.append("pr.uf = :uf")
    parametros["uf"] = uf.upper()
if cnae:
    condicoes.append("er.cnae_principal = :cnae")
    parametros["cnae"] = cnae
if valor_minimo:
    condicoes.append("pr.valor_causa >= :valor_minimo")
    parametros["valor_minimo"] = valor_minimo
if porte_opcoes[porte_escolhido]:
    condicoes.append("er.porte_codigo = :porte")
    parametros["porte"] = porte_opcoes[porte_escolhido]
if score_minimo:
    condicoes.append("l.score_lead >= :score_minimo")
    parametros["score_minimo"] = score_minimo
if status_selecionados:
    condicoes.append("l.status = ANY(:status)")
    parametros["status"] = status_selecionados

sql = f"""
    SELECT
        l.id, l.numero_cnj, l.cnpj, er.razao_social, pr.tribunal, pr.uf,
        pr.classe_nome, pr.data_ajuizamento, pr.valor_causa, er.porte_codigo,
        l.score_lead, l.status, l.metodo_match, l.score_match, l.motivo_descarte
    FROM leads l
    JOIN processos pr ON pr.numero_cnj = l.numero_cnj
    LEFT JOIN empresas_receita er ON er.cnpj = l.cnpj
    WHERE {' AND '.join(condicoes)}
    ORDER BY l.score_lead DESC NULLS LAST
    LIMIT 1000
"""

df_leads = query_df(sql, parametros)

st.divider()
st.caption(f"{len(df_leads)} lead(s) encontrados (máximo de 1000 exibidos por vez).")
st.dataframe(df_leads, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Detalhe de um lead")
if not df_leads.empty:
    id_escolhido = st.selectbox("Selecionar pelo ID", df_leads["id"].tolist())
    lead = df_leads[df_leads["id"] == id_escolhido].iloc[0]

    col_a, col_b = st.columns(2)
    with col_a:
        st.write("**Processo:**", lead["numero_cnj"])
        st.write("**Empresa:**", lead["razao_social"])
        st.write("**CNPJ:**", lead["cnpj"])
        st.write("**Tribunal/UF:**", f"{lead['tribunal']} / {lead['uf']}")
        st.write("**Classe:**", lead["classe_nome"])
        st.write("**Valor da causa:**", lead["valor_causa"])

    socios_df = query_df(
        "SELECT nome_socio, qualificacao, administrador FROM socios WHERE cnpj_basico = :b",
        {"b": lead["cnpj"][:8] if lead["cnpj"] else ""},
    )
    pgfn_df = query_df(
        "SELECT valor_consolidado, tipo_devedor, referencia_trimestre FROM pgfn_divida_ativa WHERE cnpj = :c",
        {"c": lead["cnpj"]},
    )
    with col_b:
        st.write("**Sócios:**")
        st.dataframe(socios_df, hide_index=True, use_container_width=True)
        st.write("**Dívida PGFN:**")
        st.dataframe(pgfn_df, hide_index=True, use_container_width=True)

    novo_status = st.selectbox(
        "Status", ["novo", "em_analise", "descartado", "revisao_manual"],
        index=["novo", "em_analise", "descartado", "revisao_manual"].index(lead["status"])
        if lead["status"] in ["novo", "em_analise", "descartado", "revisao_manual"] else 0,
    )
    anotacoes = st.text_area("Anotações")
    if st.button("Salvar"):
        execute(
            "UPDATE leads SET status = :status, anotacoes = :anotacoes, atualizado_em = now() WHERE id = :id",
            {"status": novo_status, "anotacoes": anotacoes, "id": int(id_escolhido)},
        )
        st.success("Atualizado.")
        st.rerun()
else:
    st.info("Nenhum lead para detalhar com os filtros atuais.")
