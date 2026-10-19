import streamlit as st

st.set_page_config(page_title="Leads PJ — Conformidade", page_icon="🛡️", layout="wide")

from app.auth import exigir_login  # noqa: E402

exigir_login()

from app.db import execute, query_df  # noqa: E402

st.title("🛡️ Conformidade (LGPD)")

aba_origem, aba_optout, aba_exclusao, aba_auditoria = st.tabs(
    ["Origem dos dados", "Lista de opt-out", "Exclusão de dados", "Log de auditoria"]
)

with aba_origem:
    st.markdown(
        """
        **De onde vêm os dados tratados aqui:**
        - **Processos e partes**: API Pública do DataJud (CNJ) — dado público,
          de processos judiciais já distribuídos (não sigilosos; processos em
          segredo de justiça são descartados automaticamente na coleta).
        - **Dados de CNPJ** (razão social, situação, sócios, endereço, telefone,
          e-mail): Dados Abertos do CNPJ, publicados pela Receita Federal.
        - **Dívida ativa**: Dados Abertos da PGFN.
        - **Contatos enriquecidos** (telefone/e-mail de sócio por CPF): quando o
          módulo de enriquecimento estiver ativo (Fase 3), via o provedor
          configurado — nunca armazenamos CPF completo, só o que o provedor
          retorna já tratado.

        Nenhum dado é comprado de corretoras de dados nem obtido por scraping de
        redes sociais — tudo tem origem em bases públicas oficiais ou no provedor
        de enriquecimento explicitamente contratado e configurado.
        """
    )

with aba_optout:
    st.caption("CNPJ ou CPF (mascarado) nesta lista é bloqueado em todo o sistema — nunca aparece em exportações.")
    df_optout = query_df("SELECT id, cnpj, cpf_mascarado, motivo, criado_em FROM opt_out ORDER BY criado_em DESC")
    st.dataframe(df_optout, use_container_width=True, hide_index=True)

    col1, col2, col3 = st.columns(3)
    cnpj_novo = col1.text_input("CNPJ (só dígitos)")
    motivo_novo = col2.text_input("Motivo")
    if col3.button("Adicionar ao opt-out") and cnpj_novo:
        execute(
            "INSERT INTO opt_out (cnpj, motivo) VALUES (:cnpj, :motivo)",
            {"cnpj": cnpj_novo, "motivo": motivo_novo},
        )
        execute(
            "INSERT INTO auditoria (acao, detalhe_json) VALUES ('opt_out_adicionado', CAST(:d AS JSONB))",
            {"d": f'{{"cnpj": "{cnpj_novo}"}}'},
        )
        st.rerun()

with aba_exclusao:
    st.warning("Isso remove permanentemente todos os dados de contato (telefone/e-mail/sócios) de um CNPJ.")
    cnpj_excluir = st.text_input("CNPJ a excluir (só dígitos)")
    if st.button("🗑️ Excluir dados de contato deste CNPJ") and cnpj_excluir:
        execute(
            "UPDATE empresas_receita SET telefone = NULL, email = NULL WHERE cnpj = :c",
            {"c": cnpj_excluir},
        )
        execute(
            "DELETE FROM socios WHERE cnpj_basico = :b", {"b": cnpj_excluir[:8]}
        )
        execute(
            "INSERT INTO auditoria (acao, detalhe_json) VALUES ('exclusao_dados', CAST(:d AS JSONB))",
            {"d": f'{{"cnpj": "{cnpj_excluir}"}}'},
        )
        st.success("Dados de contato removidos.")

    st.caption(
        "Contatos nunca usados em nenhuma exportação por mais de "
        "N dias (configurável em Configurações) também podem ser limpos automaticamente — "
        "essa rotina automática entra junto com o Módulo 6 (exportação), na Fase 3."
    )

with aba_auditoria:
    df_audit = query_df(
        "SELECT acao, detalhe_json, criado_em FROM auditoria ORDER BY criado_em DESC LIMIT 200"
    )
    st.dataframe(df_audit, use_container_width=True, hide_index=True)
