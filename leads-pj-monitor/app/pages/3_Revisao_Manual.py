import streamlit as st

st.set_page_config(page_title="Leads PJ — Revisão manual", page_icon="🔍", layout="wide")

from app.auth import exigir_login  # noqa: E402

exigir_login()

from app.db import execute, query_df  # noqa: E402

st.title("🔍 Revisão manual")
st.caption(
    "Casos em que o nome do réu não casou com confiança suficiente com nenhum CNPJ "
    "(abaixo do limiar de similaridade configurado) — decida linha a linha."
)

df = query_df(
    """
    SELECT l.id, l.numero_cnj, p.nome AS nome_no_processo, er.razao_social AS sugestao_receita,
           l.cnpj AS cnpj_sugerido, l.score_match
    FROM leads l
    JOIN partes p ON p.numero_cnj = l.numero_cnj AND p.polo = 'PASSIVO'
    LEFT JOIN empresas_receita er ON er.cnpj = l.cnpj
    WHERE l.status = 'revisao_manual'
    ORDER BY l.score_match DESC
    """
)

if df.empty:
    st.success("Nenhum caso pendente de revisão manual.")
else:
    st.dataframe(df, use_container_width=True, hide_index=True)

    id_escolhido = st.selectbox("Selecionar pelo ID pra decidir", df["id"].tolist())
    linha = df[df["id"] == id_escolhido].iloc[0]
    st.write(f"**No processo:** {linha['nome_no_processo']}")
    st.write(f"**Sugestão da Receita:** {linha['sugestao_receita']} ({linha['cnpj_sugerido']})")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Confirmar esse CNPJ"):
            execute(
                "UPDATE leads SET status = 'novo', atualizado_em = now() WHERE id = :id",
                {"id": int(id_escolhido)},
            )
            st.rerun()
    with col2:
        if st.button("❌ Descartar (não é a mesma empresa)"):
            execute(
                "UPDATE leads SET status = 'descartado', motivo_descarte = 'rejeitado na revisão manual', "
                "atualizado_em = now() WHERE id = :id",
                {"id": int(id_escolhido)},
            )
            st.rerun()
