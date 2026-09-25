"""
Configuração do ICP (perfil de cliente ideal) para este cliente — a única
fonte de verdade que muda entre um cliente e outro. Copie este arquivo para
`config.py` (já no .gitignore, então dados do cliente nunca vão pro git) e
ajuste os valores abaixo antes de rodar qualquer script da etapa 1-2.

    cp config.example.py config.py

`filtrar_cnpjs_local.py` importa este arquivo diretamente. As queries SQL
(`filtro_cnae.sql`, `socios_decisor.sql`) NÃO conseguem importar Python —
mantenha os valores abaixo sincronizados manualmente com o bloco
"CONFIGURAÇÃO" no topo de cada .sql (são os mesmos números, só duplicados
por limitação do BigQuery).
"""

# CNAEs alvo (sem pontuação, 7 dígitos) com peso: 2 = atividade
# principal/core do ICP, 1 = auxiliar/genérico (sinaliza o setor mas não é
# definitivo sozinho — ex.: desenvolvimento de software sob encomenda serve
# pra várias verticais, não só a deste cliente).
#
# Os dois códigos abaixo são só um EXEMPLO de formato — substitua pela lista
# real de CNAEs do ICP deste cliente.
CNAES_ALVO: dict[str, int] = {
    "6201501": 2,  # EXEMPLO: Desenvolvimento de programas de computador sob encomenda
    "6202300": 1,  # EXEMPLO: Desenvolvimento e licenciamento de programas de computador customizáveis
}

# Percentual mínimo (peso encontrado / peso total de CNAES_ALVO) para um
# CNPJ entrar na lista.
LIMIAR_PERCENTUAL: float = 0.6

# Teto de capital social (etapa 2 do pipeline) para focar em empresas
# pequenas/médias. Use None para não filtrar por capital social.
CAPITAL_SOCIAL_MAX: float | None = 5_000_000.0

# Código de porte na base de CNPJ (coluna `porte`/`porte_empresa`):
# 1 = Micro, 3 = Pequena, 5 = Demais. Use None para não filtrar por porte.
PORTES_ALVO: set[str] | None = None

# Termos (sem acento, minúsculos) que, aparecendo na razão social ou nome
# fantasia, também qualificam um CNPJ mesmo sem bater o limiar de CNAE.
# Comece com uma lista curta e específica — termos genéricos demais (ex.:
# "servicos", "consultoria") geram centenas de falsos positivos. Vazio =
# não usa esse critério, só o de CNAE.
PALAVRAS_CHAVE_NOME: list[str] = []

# Qualificações de sócio (tabela de códigos da Receita Federal) que indicam
# cargo de administração/direção — usadas para identificar o "decisor
# provável" de cada empresa. Náo costuma precisar mudar entre clientes.
QUALIFICACOES_DECISOR: set[str] = {"10", "16", "22", "49", "65"}
