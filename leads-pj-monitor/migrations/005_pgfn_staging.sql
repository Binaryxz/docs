-- Staging para os arquivos de dívida ativa da PGFN (Dados Abertos,
-- https://dadosabertos.pgfn.gov.br/). Layout real confirmado: CSV com
-- cabeçalho, separador ";", codificação latin1, campos SEM aspas.
CREATE TABLE IF NOT EXISTS stg_pgfn (
    cpf_cnpj               VARCHAR(30),
    tipo_pessoa              VARCHAR(20),
    tipo_devedor              VARCHAR(50),
    nome_devedor               VARCHAR(300),
    uf_devedor                  VARCHAR(2),
    unidade_responsavel           VARCHAR(150),
    numero_inscricao                VARCHAR(30),
    tipo_situacao_inscricao          VARCHAR(100),
    situacao_inscricao                 TEXT,
    receita_principal                   VARCHAR(300),
    data_inscricao                        VARCHAR(10),
    indicador_ajuizado                      VARCHAR(5),
    valor_consolidado                        VARCHAR(30)
);

CREATE INDEX IF NOT EXISTS idx_stg_pgfn_cpf_cnpj ON stg_pgfn (cpf_cnpj);

-- A situação de inscrição da PGFN vem como texto livre bem mais longo do
-- que um VARCHAR(50) (ex: "ATIVA COM PARCELAMENTO SIMPLIFICADO RESCINDIDO
-- E AJUIZAM A PROSSEGUIR") — descoberto testando com o arquivo real.
ALTER TABLE pgfn_divida_ativa ALTER COLUMN situacao TYPE TEXT;

-- O arquivo de FGTS tem 2 colunas extras (ENTIDADE_RESPONSAVEL,
-- UNIDADE_INSCRICAO) que os arquivos Não Previdenciário/Previdenciário não
-- têm — também descoberto testando o arquivo real. Staging própria.
CREATE TABLE IF NOT EXISTS stg_pgfn_fgts (
    cpf_cnpj               VARCHAR(30),
    tipo_pessoa              VARCHAR(20),
    tipo_devedor              VARCHAR(50),
    nome_devedor               VARCHAR(300),
    uf_devedor                  VARCHAR(2),
    unidade_responsavel           VARCHAR(150),
    entidade_responsavel           VARCHAR(100),
    unidade_inscricao                VARCHAR(100),
    numero_inscricao                   VARCHAR(30),
    tipo_situacao_inscricao             VARCHAR(100),
    situacao_inscricao                   TEXT,
    receita_principal                     VARCHAR(300),
    data_inscricao                          VARCHAR(10),
    indicador_ajuizado                        VARCHAR(5),
    valor_consolidado                          VARCHAR(30)
);
