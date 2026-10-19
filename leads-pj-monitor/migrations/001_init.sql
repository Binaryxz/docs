-- Schema inicial do leads-pj-monitor.
-- Convenção: tudo em português nos nomes visíveis ao usuário (telas), inglês/português
-- misto nas tabelas internas conforme já vinha sendo usado no restante do projeto.

CREATE TABLE IF NOT EXISTS processos (
    numero_cnj          VARCHAR(25) PRIMARY KEY,
    tribunal            VARCHAR(30) NOT NULL,          -- alias DataJud, ex: api_publica_tjsp
    classe_codigo       INTEGER,
    classe_nome         VARCHAR(200),
    assunto_codigo       INTEGER,
    assunto_nome         VARCHAR(300),
    data_ajuizamento     TIMESTAMPTZ,
    data_descoberta      TIMESTAMPTZ NOT NULL DEFAULT now(),  -- quando ENTROU neste sistema
    valor_causa          NUMERIC(18, 2),
    orgao_julgador       VARCHAR(300),
    grau                 VARCHAR(10),
    sigiloso             BOOLEAN NOT NULL DEFAULT false,
    bruto_json           JSONB,                         -- payload original do DataJud, auditoria
    criado_em            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_processos_tribunal ON processos (tribunal);
CREATE INDEX IF NOT EXISTS idx_processos_data_ajuizamento ON processos (data_ajuizamento);
CREATE INDEX IF NOT EXISTS idx_processos_classe ON processos (classe_codigo);

CREATE TABLE IF NOT EXISTS partes (
    id                  SERIAL PRIMARY KEY,
    numero_cnj          VARCHAR(25) NOT NULL REFERENCES processos(numero_cnj) ON DELETE CASCADE,
    polo                VARCHAR(10) NOT NULL,           -- ATIVO / PASSIVO
    nome                VARCHAR(300) NOT NULL,
    cnpj                VARCHAR(14),                    -- só dígitos, quando o tribunal expõe
    cpf_mascarado        VARCHAR(20),                    -- quando só o CPF mascarado está disponível
    origem               VARCHAR(30) NOT NULL,           -- 'datajud' ou 'scraper:<tribunal>'
    criado_em            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (numero_cnj, polo, nome)
);

CREATE INDEX IF NOT EXISTS idx_partes_cnpj ON partes (cnpj);
CREATE INDEX IF NOT EXISTS idx_partes_numero_cnj ON partes (numero_cnj);

CREATE TABLE IF NOT EXISTS empresas_receita (
    cnpj                 VARCHAR(14) PRIMARY KEY,
    cnpj_basico           VARCHAR(8) NOT NULL,
    razao_social          VARCHAR(300),
    nome_fantasia         VARCHAR(300),
    natureza_juridica_codigo INTEGER,
    situacao_cadastral    VARCHAR(20),
    data_situacao_cadastral DATE,
    data_inicio_atividade DATE,
    capital_social        NUMERIC(18, 2),
    porte_codigo          VARCHAR(2),                   -- 00/01/03/05 (Receita)
    cnae_principal        VARCHAR(10),
    uf                    VARCHAR(2),
    municipio             VARCHAR(150),
    logradouro            VARCHAR(300),
    telefone              VARCHAR(30),
    email                 VARCHAR(200),
    atualizado_em          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_empresas_receita_razao_social ON empresas_receita (razao_social);
CREATE INDEX IF NOT EXISTS idx_empresas_receita_basico ON empresas_receita (cnpj_basico);
CREATE INDEX IF NOT EXISTS idx_empresas_receita_uf ON empresas_receita (uf);

CREATE TABLE IF NOT EXISTS empresas_filiais_count (
    cnpj_basico          VARCHAR(8) PRIMARY KEY,
    total_filiais_ativas  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS socios (
    id                   SERIAL PRIMARY KEY,
    cnpj_basico           VARCHAR(8) NOT NULL,
    nome_socio            VARCHAR(300),
    cpf_mascarado          VARCHAR(20),
    qualificacao           VARCHAR(200),
    administrador          BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_socios_cnpj_basico ON socios (cnpj_basico);

CREATE TABLE IF NOT EXISTS pgfn_divida_ativa (
    id                   SERIAL PRIMARY KEY,
    cnpj                 VARCHAR(14) NOT NULL,
    valor_consolidado     NUMERIC(18, 2),
    tipo_devedor          VARCHAR(50),
    situacao              VARCHAR(50),
    referencia_trimestre   VARCHAR(10),                 -- ex: 2026-Q3
    atualizado_em          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (cnpj, referencia_trimestre)
);

CREATE INDEX IF NOT EXISTS idx_pgfn_cnpj ON pgfn_divida_ativa (cnpj);

CREATE TABLE IF NOT EXISTS marcas_grandes_exclusao (
    id                   SERIAL PRIMARY KEY,
    termo                 VARCHAR(200) NOT NULL,         -- substring (uppercase, sem acento) OU cnpj/cnpj_basico
    tipo                  VARCHAR(20) NOT NULL DEFAULT 'substring',  -- 'substring' | 'cnpj' | 'cnpj_basico'
    criado_em              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leads (
    id                    SERIAL PRIMARY KEY,
    numero_cnj             VARCHAR(25) NOT NULL REFERENCES processos(numero_cnj) ON DELETE CASCADE,
    cnpj                   VARCHAR(14) REFERENCES empresas_receita(cnpj),
    metodo_match            VARCHAR(20),                  -- 'cnpj_exato' | 'fuzzy'
    score_match             NUMERIC(5, 2),                 -- score de similaridade rapidfuzz (0-100), quando fuzzy
    score_lead              NUMERIC(5, 2),                 -- lead score final (0-100)
    status                  VARCHAR(20) NOT NULL DEFAULT 'novo',  -- novo | em_analise | descartado | revisao_manual
    motivo_descarte          VARCHAR(300),
    anotacoes                TEXT,
    criado_em                TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (numero_cnj, cnpj)
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads (score_lead);
CREATE INDEX IF NOT EXISTS idx_leads_cnpj ON leads (cnpj);

CREATE TABLE IF NOT EXISTS nichos_salvos (
    id                   SERIAL PRIMARY KEY,
    nome                  VARCHAR(100) NOT NULL UNIQUE,
    filtros_json           JSONB NOT NULL,
    criado_em               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS opt_out (
    id                   SERIAL PRIMARY KEY,
    cnpj                  VARCHAR(14),
    cpf_mascarado          VARCHAR(20),
    motivo                 VARCHAR(300),
    criado_em               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_opt_out_cnpj ON opt_out (cnpj);

CREATE TABLE IF NOT EXISTS auditoria (
    id                   SERIAL PRIMARY KEY,
    acao                  VARCHAR(50) NOT NULL,           -- 'consulta_paga' | 'exportacao' | 'exclusao_dados'
    detalhe_json            JSONB,
    criado_em               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gasto_enriquecimento (
    id                   SERIAL PRIMARY KEY,
    provedor               VARCHAR(50) NOT NULL,
    cnpj                   VARCHAR(14),
    custo                  NUMERIC(10, 4) NOT NULL,
    criado_em               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gasto_criado_em ON gasto_enriquecimento (criado_em);

CREATE TABLE IF NOT EXISTS cache_enriquecimento (
    cnpj                  VARCHAR(14) PRIMARY KEY,
    provedor                VARCHAR(50) NOT NULL,
    resultado_json           JSONB,
    atualizado_em             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execucoes_coleta (
    id                    SERIAL PRIMARY KEY,
    tipo                   VARCHAR(30) NOT NULL,          -- 'datajud' | 'receita' | 'pgfn' | 'scraper_partes'
    tribunal                VARCHAR(30),
    iniciado_em              TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalizado_em             TIMESTAMPTZ,
    status                   VARCHAR(20) NOT NULL DEFAULT 'rodando',  -- rodando | sucesso | erro
    processos_novos           INTEGER DEFAULT 0,
    erro_detalhe              TEXT
);

CREATE INDEX IF NOT EXISTS idx_execucoes_tipo_tribunal ON execucoes_coleta (tipo, tribunal);

CREATE TABLE IF NOT EXISTS config_kv (
    chave                 VARCHAR(100) PRIMARY KEY,
    valor_json              JSONB NOT NULL,
    atualizado_em             TIMESTAMPTZ NOT NULL DEFAULT now()
);
