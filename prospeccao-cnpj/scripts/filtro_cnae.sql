-- Filtro de leads na base pública de CNPJ (Receita Federal) via BigQuery.
-- Fonte dos dados: projeto público `basedosdados.br_me_cnpj`
-- (basedosdados.org), que replica os arquivos abertos da Receita Federal
-- (Empresas, Estabelecimentos, Sócios).
--
-- Etapas 1 (CNAE) + 2 (capital social/porte) do pipeline num só SELECT.
--
-- IMPORTANTE — estabelecimentos/empresas/socios são snapshots MENSAIS
-- completos (particionados por `data`, clusterizados por `ano,mes`). Sem
-- filtrar `ano`/`mes`, esta query escaneia TODO o histórico (centenas de
-- GB). Rode descobrir_periodo.sql primeiro e ajuste ano_referencia/
-- mes_referencia abaixo antes de rodar isto de verdade.
--
-- Nomes de coluna que divergem do que "faria sentido" assumir (validado em
-- 2026-09 — não repita o erro de assumir uf/municipio/correio_eletronico ou
-- situacao_cadastral com zero à esquerda, que são os nomes/formato usados
-- no layout ORIGINAL da Receita, não neste dataset):
--   estabelecimentos.email          (não correio_eletronico)
--   estabelecimentos.sigla_uf       (não uf)
--   estabelecimentos.id_municipio   (código IBGE, não nome; não municipio)
--   situacao_cadastral = '2'        (sem zero à esquerda; não '02')
--
-- ============================= CONFIGURAÇÃO ==============================
-- Mantenha este bloco sincronizado com config.example.py/config.py — o
-- BigQuery não consegue importar Python, então os valores são duplicados
-- aqui manualmente.

DECLARE ano_referencia INT64 DEFAULT 2026;  -- <- preencha com o resultado de descobrir_periodo.sql
DECLARE mes_referencia INT64 DEFAULT 1;     -- <- idem

-- ICP: contadores/escritórios de contabilidade (CNAE 69.20-6, CONCLA/IBGE,
-- validado em 2026-09). As duas únicas subclasses dessa classe — sem CNAE
-- auxiliar/genérico, pesam igual (mesmos valores de config.py).
DECLARE cnaes_alvo ARRAY<STRUCT<codigo STRING, peso INT64>> DEFAULT [
  STRUCT('6920601' AS codigo, 2 AS peso), -- Atividades de contabilidade
  STRUCT('6920602' AS codigo, 2 AS peso)  -- Atividades de consultoria e auditoria contábil e tributária
];
-- 0.5 (não 0.6): com só 2 CNAEs de peso igual, um escritório real costuma
-- declarar SÓ UM dos dois como principal — precisa entrar com só 1 match.
DECLARE limiar_percentual FLOAT64 DEFAULT 0.5;          -- = LIMIAR_PERCENTUAL
DECLARE capital_social_max FLOAT64 DEFAULT NULL;        -- = CAPITAL_SOCIAL_MAX (sem teto — contadores de qualquer porte)
DECLARE portes_alvo ARRAY<STRING> DEFAULT [];           -- = PORTES_ALVO (vazio = não filtra; códigos: 1=Micro,3=Pequena,5=Demais)
DECLARE palavras_chave_nome ARRAY<STRING> DEFAULT [];   -- = PALAVRAS_CHAVE_NOME (vazio — CNAE já é 100% específico do setor)
-- ==========================================================================
-- Lote piloto: BigQuery não aceita variável no LIMIT (só literal), por isso
-- o "500" está direto na cláusula LIMIT no final da query, não aqui em cima
-- — pra pegar a base inteira depois de validar a amostra, edite ali.

DECLARE peso_total_alvo INT64 DEFAULT (SELECT SUM(peso) FROM UNNEST(cnaes_alvo));

WITH estabelecimentos_periodo AS (
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
    e.email,
    e.sigla_uf,
    e.id_municipio,
    ARRAY_CONCAT(
      [e.cnae_fiscal_principal],
      IFNULL(SPLIT(e.cnae_fiscal_secundaria, ','), [])
    ) AS todos_cnaes
  FROM `basedosdados.br_me_cnpj.estabelecimentos` AS e
  WHERE e.ano = ano_referencia
    AND e.mes = mes_referencia
    AND e.situacao_cadastral = '2' -- 2 = ATIVA (sem zero à esquerda nesta base)
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
  FROM estabelecimentos_periodo
),

candidatos AS (
  SELECT
    m.*,
    ROUND(m.peso_cnaes_alvo_encontrados / peso_total_alvo, 2) AS percentual_match_cnae
  FROM match_cnae AS m
  JOIN `basedosdados.br_me_cnpj.empresas` AS emp
    ON emp.cnpj_basico = m.cnpj_basico
   AND emp.ano = ano_referencia
   AND emp.mes = mes_referencia
  WHERE
    (
      m.peso_cnaes_alvo_encontrados / peso_total_alvo >= limiar_percentual
      OR (
        ARRAY_LENGTH(palavras_chave_nome) > 0
        AND EXISTS(
          SELECT 1 FROM UNNEST(palavras_chave_nome) AS p
          WHERE LOWER(emp.razao_social) LIKE CONCAT('%', p, '%')
             OR LOWER(IFNULL(m.nome_fantasia, '')) LIKE CONCAT('%', p, '%')
        )
      )
    )
    AND (capital_social_max IS NULL OR emp.capital_social <= capital_social_max)
    AND (ARRAY_LENGTH(portes_alvo) = 0 OR emp.porte IN UNNEST(portes_alvo))
)

SELECT
  CONCAT(c.cnpj_basico, c.cnpj_ordem, c.cnpj_dv) AS cnpj,
  emp.razao_social,
  c.nome_fantasia,
  emp.capital_social,
  emp.porte,
  c.percentual_match_cnae,
  c.qtd_cnaes_alvo_encontrados,
  c.cnae_fiscal_principal,
  c.cnae_fiscal_secundaria,
  c.data_inicio_atividade,
  c.sigla_uf AS uf,
  c.id_municipio, -- código IBGE — junte com `basedosdados.br_bd_diretorios_brasil.municipio` se precisar do nome
  c.ddd_1,
  c.telefone_1,
  c.ddd_2,
  c.telefone_2,
  c.email
FROM candidatos AS c
JOIN `basedosdados.br_me_cnpj.empresas` AS emp
  ON emp.cnpj_basico = c.cnpj_basico
 AND emp.ano = ano_referencia
 AND emp.mes = mes_referencia
-- Prioriza quem já tem telefone/e-mail declarado (lote piloto é mais útil
-- assim), depois quem bate os dois CNAEs (consultoria+auditoria, não só
-- contabilidade básica), depois capital social maior.
ORDER BY
  (c.telefone_1 IS NOT NULL AND c.telefone_1 != '') DESC,
  (c.email IS NOT NULL AND c.email != '') DESC,
  c.percentual_match_cnae DESC,
  emp.capital_social DESC
LIMIT 500; -- lote piloto — comente esta linha (ou aumente o número) pra rodar a base inteira
