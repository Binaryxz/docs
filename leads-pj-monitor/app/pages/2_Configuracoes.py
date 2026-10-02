import streamlit as st

st.set_page_config(page_title="Leads PJ — Configurações", page_icon="⚙️", layout="wide")

from app.auth import exigir_login, trocar_senha  # noqa: E402

exigir_login()

from app.config import get_all_config, set_config  # noqa: E402
from app.db import execute, query_df  # noqa: E402
from app.scheduler import reagendar_coleta_diaria  # noqa: E402
from app.tribunais import TRIBUNAL_PARA_UF  # noqa: E402
from app.tpu import buscar_classes  # noqa: E402

st.title("⚙️ Configurações")
cfg = get_all_config()

aba_coleta, aba_filtros, aba_score, aba_enriquecimento, aba_sistema = st.tabs(
    ["Coleta (tribunais/classes)", "Filtros de qualificação", "Pesos do score", "Enriquecimento", "Sistema"]
)

with aba_coleta:
    st.subheader("Tribunais ativos")
    aliases_disponiveis = sorted(TRIBUNAL_PARA_UF.keys())
    tribunais_ativos = st.multiselect(
        "Tribunais de Justiça estaduais monitorados",
        aliases_disponiveis,
        default=cfg.get("tribunais_ativos", []),
    )
    if st.button("Salvar tribunais"):
        set_config("tribunais_ativos", tribunais_ativos)
        st.success("Salvo.")

    st.subheader("Classes processuais (TPU)")
    termo_busca = st.text_input("Buscar classe por nome")
    classes_encontradas = buscar_classes(termo_busca)
    st.caption("Clique nos códigos abaixo pra adicionar/remover da lista monitorada.")
    classes_atuais = {c["codigo"]: c["nome"] for c in cfg.get("classes_tpu", [])}
    for classe in classes_encontradas[:30]:
        marcado = classe["codigo"] in classes_atuais
        if st.checkbox(f"{classe['codigo']} — {classe['nome']}", value=marcado, key=f"classe_{classe['codigo']}"):
            classes_atuais[classe["codigo"]] = classe["nome"]
        elif classe["codigo"] in classes_atuais:
            del classes_atuais[classe["codigo"]]
    if st.button("Salvar classes"):
        set_config("classes_tpu", [{"codigo": k, "nome": v} for k, v in classes_atuais.items()])
        st.success("Salvo.")

    st.subheader("Janela de busca e ritmo")
    dias_retroativos = st.number_input(
        "Buscar processos ajuizados nos últimos N dias", min_value=1, value=cfg.get("dias_retroativos_busca", 7)
    )
    ritmo = st.number_input(
        "Intervalo entre requisições (segundos)", min_value=0.5, value=float(cfg.get("ritmo_requisicao_segundos", 2.0))
    )
    limite_atraso = st.number_input(
        "Alertar se atraso médio passar de (dias)", min_value=1, value=cfg.get("limite_atraso_alerta_dias", 5)
    )
    if st.button("Salvar janela/ritmo"):
        set_config("dias_retroativos_busca", dias_retroativos)
        set_config("ritmo_requisicao_segundos", ritmo)
        set_config("limite_atraso_alerta_dias", limite_atraso)
        st.success("Salvo.")

with aba_filtros:
    st.subheader("Filtros de exclusão")
    somente_pj = st.checkbox("Considerar somente pessoa jurídica", value=cfg.get("filtro_somente_pj", True))
    situacao_ativa = st.checkbox("Exigir situação cadastral ATIVA", value=cfg.get("filtro_situacao_ativa", True))
    excluir_grandes = st.checkbox(
        "Excluir empresas grandes", value=cfg.get("filtro_excluir_empresas_grandes", True)
    )
    teto_capital = st.number_input(
        "Teto de capital social (R$)", min_value=0.0, value=float(cfg.get("teto_capital_social", 10_000_000))
    )
    limite_filiais = st.number_input(
        "Limite de filiais ativas", min_value=1, value=cfg.get("limite_filiais_ativas", 10)
    )
    somente_me_epp = st.checkbox("Manter somente ME e EPP", value=cfg.get("filtro_somente_me_epp", False))
    valor_minimo = st.number_input(
        "Valor mínimo da causa (R$)", min_value=0.0, value=float(cfg.get("valor_causa_minimo", 0))
    )
    divida_minima = st.number_input(
        "Dívida PGFN mínima (R$)", min_value=0.0, value=float(cfg.get("divida_pgfn_minima", 0))
    )
    if st.button("Salvar filtros"):
        set_config("filtro_somente_pj", somente_pj)
        set_config("filtro_situacao_ativa", situacao_ativa)
        set_config("filtro_excluir_empresas_grandes", excluir_grandes)
        set_config("teto_capital_social", teto_capital)
        set_config("limite_filiais_ativas", limite_filiais)
        set_config("filtro_somente_me_epp", somente_me_epp)
        set_config("valor_causa_minimo", valor_minimo)
        set_config("divida_pgfn_minima", divida_minima)
        st.success("Salvo.")

    st.subheader("Lista de exclusão de marca grande")
    df_marcas = query_df("SELECT id, termo, tipo FROM marcas_grandes_exclusao ORDER BY termo")
    st.dataframe(df_marcas, use_container_width=True, hide_index=True, height=250)
    col_novo1, col_novo2, col_novo3 = st.columns([2, 1, 1])
    novo_termo = col_novo1.text_input("Novo termo (substring em MAIÚSCULAS, sem acento)")
    novo_tipo = col_novo2.selectbox("Tipo", ["substring", "cnpj", "cnpj_basico"])
    if col_novo3.button("Adicionar") and novo_termo:
        execute(
            "INSERT INTO marcas_grandes_exclusao (termo, tipo) VALUES (:t, :tipo)",
            {"t": novo_termo.upper(), "tipo": novo_tipo},
        )
        st.rerun()

with aba_score:
    st.subheader("Pesos do lead score (soma não precisa ser 100 — é só a proporção que importa)")
    pesos = cfg.get("pesos_score", {})
    novo_valor_causa = st.slider("Valor da causa", 0.0, 1.0, float(pesos.get("valor_causa", 0.3)))
    novo_divida = st.slider("Dívida PGFN", 0.0, 1.0, float(pesos.get("divida_pgfn", 0.25)))
    novo_porte = st.slider("Porte", 0.0, 1.0, float(pesos.get("porte", 0.15)))
    novo_capital = st.slider("Capital social", 0.0, 1.0, float(pesos.get("capital_social", 0.10)))
    novo_execucoes = st.slider(
        "Nº de execuções recentes do mesmo CNPJ", 0.0, 1.0, float(pesos.get("numero_execucoes_recentes", 0.20))
    )
    if st.button("Salvar pesos"):
        set_config(
            "pesos_score",
            {
                "valor_causa": novo_valor_causa,
                "divida_pgfn": novo_divida,
                "porte": novo_porte,
                "capital_social": novo_capital,
                "numero_execucoes_recentes": novo_execucoes,
            },
        )
        st.success("Salvo.")

    st.subheader("Matching por nome (fuzzy)")
    limiar = st.slider(
        "Limiar de similaridade pra aceitar automaticamente (abaixo disso vai pra revisão manual)",
        0, 100, cfg.get("limiar_similaridade_fuzzy", 85),
    )
    if st.button("Salvar limiar"):
        set_config("limiar_similaridade_fuzzy", limiar)
        st.success("Salvo.")

with aba_enriquecimento:
    st.info(
        "Enriquecimento de contatos (FonteData / BigDataCorp) é a Fase 3 deste projeto — "
        "ainda não implementado nesta versão. Os campos abaixo já ficam salvos pra quando "
        "o módulo entrar."
    )
    provedor = st.selectbox(
        "Provedor ativo", ["fontedata", "bigdatacorp"],
        index=["fontedata", "bigdatacorp"].index(cfg.get("provedor_enriquecimento_ativo", "fontedata")),
    )
    chave_api = st.text_input("Chave de API do provedor", type="password")
    teto_diario = st.number_input("Teto de gasto diário (R$)", min_value=0.0, value=float(cfg.get("teto_gasto_diario", 50.0)))
    teto_mensal = st.number_input("Teto de gasto mensal (R$)", min_value=0.0, value=float(cfg.get("teto_gasto_mensal", 800.0)))
    score_minimo_enriquecer = st.slider(
        "Enriquecer somente leads com score acima de", 0, 100, cfg.get("score_minimo_para_enriquecer", 50)
    )
    if st.button("Salvar enriquecimento"):
        set_config("provedor_enriquecimento_ativo", provedor)
        set_config("teto_gasto_diario", teto_diario)
        set_config("teto_gasto_mensal", teto_mensal)
        set_config("score_minimo_para_enriquecer", score_minimo_enriquecer)
        if chave_api:
            from app.config import cifrar

            set_config(f"chave_api_{provedor}", cifrar(chave_api))
        st.success("Salvo.")

with aba_sistema:
    st.subheader("Horário da coleta diária")
    horario = st.text_input("Horário (HH:MM, horário de Brasília)", value=cfg.get("horario_execucao_diaria", "06:00"))
    if st.button("Salvar horário"):
        set_config("horario_execucao_diaria", horario)
        reagendar_coleta_diaria(horario)
        st.success("Salvo e já reagendado.")

    st.subheader("Importações pesadas (manuais)")
    st.caption(
        "Essas rotinas também rodam automaticamente (Receita: todo dia 1 às 3h; "
        "PGFN: trimestralmente) — use os botões só se quiser forçar uma atualização agora."
    )
    col_imp1, col_imp2 = st.columns(2)
    with col_imp1:
        if st.button("📥 Importar Receita Federal agora (pode levar horas)"):
            from app.scheduler import _rodar_import_receita

            import threading

            threading.Thread(target=_rodar_import_receita, daemon=True).start()
            st.success("Disparado em segundo plano.")
    with col_imp2:
        if st.button("📥 Importar PGFN agora (pode levar bastante tempo)"):
            from app.scheduler import _rodar_import_pgfn

            import threading

            threading.Thread(target=_rodar_import_pgfn, daemon=True).start()
            st.success("Disparado em segundo plano.")

    st.subheader("Trocar senha de acesso")
    nova_senha = st.text_input("Nova senha", type="password")
    if st.button("Trocar senha") and nova_senha:
        trocar_senha(nova_senha)
        st.success("Senha trocada.")
