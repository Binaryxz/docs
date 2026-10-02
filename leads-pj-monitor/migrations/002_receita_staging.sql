-- Tabelas de apoio para o import dos dados abertos do CNPJ (Receita Federal).
-- As "stg_*" são de carga (TRUNCATE + COPY a cada shard, acumulando os 10
-- arquivos de cada dataset antes do JOIN final) — não ficam permanentes com
-- dados de produção, só durante o import.

CREATE TABLE IF NOT EXISTS municipios_rf (
    codigo VARCHAR(10) PRIMARY KEY,
    nome   VARCHAR(150) NOT NULL
);

CREATE TABLE IF NOT EXISTS qualificacoes_rf (
    codigo VARCHAR(10) PRIMARY KEY,
    nome   VARCHAR(200) NOT NULL
);

CREATE TABLE IF NOT EXISTS stg_empresas (
    cnpj_basico            VARCHAR(8),
    razao_social            VARCHAR(300),
    natureza_juridica_codigo VARCHAR(10),
    qualificacao_responsavel VARCHAR(10),
    capital_social           VARCHAR(30),   -- vem com vírgula decimal; convertido no JOIN final
    porte_codigo             VARCHAR(2),
    ente_federativo          VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS stg_estabelecimentos (
    cnpj_basico              VARCHAR(8),
    cnpj_ordem                VARCHAR(4),
    cnpj_dv                   VARCHAR(2),
    identificador_matriz_filial VARCHAR(2),
    nome_fantasia              VARCHAR(300),
    situacao_cadastral          VARCHAR(2),
    data_situacao_cadastral      VARCHAR(10),
    motivo_situacao_cadastral     VARCHAR(10),
    nome_cidade_exterior           VARCHAR(150),
    pais                             VARCHAR(10),
    data_inicio_atividade            VARCHAR(10),
    cnae_fiscal_principal             VARCHAR(10),
    cnae_fiscal_secundaria             VARCHAR(2000),
    tipo_logradouro                     VARCHAR(50),
    logradouro                           VARCHAR(300),
    numero                                VARCHAR(20),
    complemento                           VARCHAR(200),
    bairro                                 VARCHAR(150),
    cep                                     VARCHAR(10),
    uf                                       VARCHAR(2),
    municipio_codigo                         VARCHAR(10),
    ddd1                                       VARCHAR(5),
    telefone1                                   VARCHAR(20),
    ddd2                                         VARCHAR(5),
    telefone2                                     VARCHAR(20),
    ddd_fax                                        VARCHAR(5),
    fax                                              VARCHAR(20),
    correio_eletronico                               VARCHAR(200),
    situacao_especial                                 VARCHAR(100),
    data_situacao_especial                             VARCHAR(10)
);

CREATE INDEX IF NOT EXISTS idx_stg_estab_basico ON stg_estabelecimentos (cnpj_basico);
CREATE INDEX IF NOT EXISTS idx_stg_emp_basico ON stg_empresas (cnpj_basico);

CREATE TABLE IF NOT EXISTS stg_socios (
    cnpj_basico           VARCHAR(8),
    identificador_socio    VARCHAR(2),
    nome_socio              VARCHAR(300),
    cpf_cnpj_socio           VARCHAR(20),
    qualificacao_socio        VARCHAR(10),
    data_entrada_sociedade     VARCHAR(10),
    pais                         VARCHAR(10),
    representante_legal           VARCHAR(20),
    nome_representante             VARCHAR(300),
    qualificacao_representante      VARCHAR(10),
    faixa_etaria                     VARCHAR(2)
);

CREATE INDEX IF NOT EXISTS idx_stg_socios_basico ON stg_socios (cnpj_basico);
