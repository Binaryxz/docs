-- Filtro de leads fintech na base pública de CNPJ (Receita Federal) via BigQuery.
-- Fonte dos dados: projeto público "basedosdados" (basedosdados.org), que replica
-- os arquivos oficiais da Receita Federal (Empresas, Estabelecimentos, Sócios).
--
-- Regra de negócio (etapa 1 do fluxo):
--   entra no resultado todo CNPJ cuja soma de pesos dos CNAEs alvo batidos
--   (principal + secundários) seja PELO MENOS 60% da soma total de pesos,
--   OU cuja razão social/nome fantasia contenha "fintech".
--
-- Peso 2 (principal/financeiro/core): CNAEs de atividade financeira e o de
--   suporte técnico em TI (peso pleno na análise).
-- Peso 1 (auxiliar/genérico): CNAEs de desenvolvimento de software e
--   "outras atividades de serviços prestados às empresas" — sinalizam uma
--   empresa de tecnologia/serviços B2B em geral, não necessariamente uma
--   fintech, então pesam menos que os de atividade financeira.

DECLARE cnaes_alvo ARRAY<STRUCT<codigo STRING, peso INT64>> DEFAULT [
  STRUCT('7490104' AS codigo, 2 AS peso), -- Principal: intermediação e agenciamento de serviços e negócios em geral
  STRUCT('6619302' AS codigo, 2 AS peso), -- Principal: correspondentes de instituições financeiras
  STRUCT('6619399' AS codigo, 2 AS peso), -- Outras atividades auxiliares dos serviços financeiros não especificadas anteriormente
  STRUCT('8291100' AS codigo, 2 AS peso), -- Atividades de cobranças e informações cadastrais
  STRUCT('6461100' AS codigo, 2 AS peso), -- Holdings de instituições financeiras
  STRUCT('6492100' AS codigo, 2 AS peso), -- Securitização de créditos
  STRUCT('6619305' AS codigo, 2 AS peso), -- Operadoras de cartões de débito
  STRUCT('6619306' AS codigo, 2 AS peso), -- Casas de câmbio
  STRUCT('6612603' AS codigo, 2 AS peso), -- Corretoras de câmbio
  STRUCT('6209100' AS codigo, 2 AS peso), -- Suporte técnico, manutenção e outros serviços em TI
  STRUCT('6203100' AS codigo, 1 AS peso), -- Desenvolvimento e licenciamento de programas de computador não-customizáveis
  STRUCT('6201501' AS codigo, 1 AS peso), -- Desenvolvimento de programas de computador sob encomenda
  STRUCT('8299799' AS codigo, 1 AS peso)  -- Outras atividades de serviços prestados às empresas
];

DECLARE peso_total_alvo INT64 DEFAULT (SELECT SUM(peso) FROM UNNEST(cnaes_alvo));
DECLARE limiar_percentual FLOAT64 DEFAULT 0.6;

WITH estabelecimentos_cnaes AS (
  SELECT
    e.cnpj_basico,
    e.cnpj_ordem,
    e.cnpj_dv,
    e.identificador_matriz_filial,
    e.nome_fantasia,
    e.situacao_cadastral,
    e.data_inicio_atividade,
    e.cnae_fiscal_principal,
    e.cnae_fiscal_secundaria,
    e.ddd_1,
    e.telefone_1,
    e.ddd_2,
    e.telefone_2,
    e.correio_eletronico,
    e.uf,
    e.municipio,
    -- CNAE principal + todos os secundários (string separada por vírgula) em um único array
    ARRAY_CONCAT(
      [e.cnae_fiscal_principal],
      IFNULL(SPLIT(e.cnae_fiscal_secundaria, ','), [])
    ) AS todos_cnaes
  FROM `basedosdados.br_me_cnpj.estabelecimentos` AS e
  WHERE e.situacao_cadastral = '02' -- 02 = ATIVA
),

match_cnae AS (
  SELECT
    *,
    ARRAY_LENGTH(
      ARRAY(SELECT c FROM UNNEST(todos_cnaes) AS c WHERE c IN (SELECT codigo FROM UNNEST(cnaes_alvo)))
    ) AS qtd_cnaes_alvo_encontrados,
    (
      SELECT IFNULL(SUM(a.peso), 0)
      FROM UNNEST(cnaes_alvo) AS a
      WHERE a.codigo IN UNNEST(todos_cnaes)
    ) AS peso_cnaes_alvo_encontrados
  FROM estabelecimentos_cnaes
),

candidatos AS (
  SELECT
    m.*,
    ROUND(m.peso_cnaes_alvo_encontrados / peso_total_alvo, 2) AS percentual_match_cnae
  FROM match_cnae AS m
  JOIN `basedosdados.br_me_cnpj.empresas` AS emp
    ON emp.cnpj_basico = m.cnpj_basico
  WHERE
    m.peso_cnaes_alvo_encontrados / peso_total_alvo >= limiar_percentual
    OR LOWER(emp.razao_social) LIKE '%fintech%'
    OR LOWER(m.nome_fantasia) LIKE '%fintech%'
)

SELECT
  CONCAT(c.cnpj_basico, c.cnpj_ordem, c.cnpj_dv) AS cnpj,
  emp.razao_social,
  c.nome_fantasia,
  c.percentual_match_cnae,
  c.qtd_cnaes_alvo_encontrados,
  c.cnae_fiscal_principal,
  c.cnae_fiscal_secundaria,
  c.data_inicio_atividade,
  c.uf,
  c.municipio,
  c.ddd_1,
  c.telefone_1,
  c.ddd_2,
  c.telefone_2,
  c.correio_eletronico
FROM candidatos AS c
JOIN `basedosdados.br_me_cnpj.empresas` AS emp
  ON emp.cnpj_basico = c.cnpj_basico
ORDER BY c.percentual_match_cnae DESC;
