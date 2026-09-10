-- Filtro de leads fintech na base pública de CNPJ (Receita Federal) via BigQuery.
-- Fonte dos dados: projeto público "basedosdados" (basedosdados.org), que replica
-- os arquivos oficiais da Receita Federal (Empresas, Estabelecimentos, Sócios).
--
-- Regra de negócio (etapa 1 do fluxo):
--   entra no resultado todo CNPJ que tenha PELO MENOS 60% dos 9 CNAEs alvo
--   registrados entre seu CNAE principal + secundários, OU cuja razão social/
--   nome fantasia contenha "fintech".
--
-- 9 CNAEs alvo => limiar de 60% = pelo menos 6 dos 9 códigos presentes no CNPJ.

DECLARE cnaes_alvo ARRAY<STRING> DEFAULT [
  '7490104', -- Principal: intermediação e agenciamento de serviços e negócios em geral
  '6203100', -- Desenvolvimento e licenciamento de programas de computador não-customizáveis
  '7020400', -- Consultoria em gestão empresarial
  '6201501', -- Desenvolvimento de programas de computador sob encomenda
  '8299799', -- Outras atividades de serviços prestados às empresas
  '6209100', -- Suporte técnico, manutenção e outros serviços em TI
  '6619399', -- Outras atividades auxiliares dos serviços financeiros não especificadas anteriormente
  '6619302', -- Correspondentes de instituições financeiras
  '8291100'  -- Atividades de cobranças e informações cadastrais
];

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
      ARRAY(SELECT c FROM UNNEST(todos_cnaes) AS c WHERE c IN UNNEST(cnaes_alvo))
    ) AS qtd_cnaes_alvo_encontrados
  FROM estabelecimentos_cnaes
),

candidatos AS (
  SELECT
    m.*,
    ROUND(m.qtd_cnaes_alvo_encontrados / ARRAY_LENGTH(cnaes_alvo), 2) AS percentual_match_cnae
  FROM match_cnae AS m
  JOIN `basedosdados.br_me_cnpj.empresas` AS emp
    ON emp.cnpj_basico = m.cnpj_basico
  WHERE
    m.qtd_cnaes_alvo_encontrados / ARRAY_LENGTH(cnaes_alvo) >= limiar_percentual
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
