-- Etapa 2 (parte 2): sócios e decisor provável de cada CNPJ já filtrado.
-- Rode depois de salvar o resultado de filtro_cnae.sql em uma tabela
-- (ex.: `meu_projeto.prospeccao.leads_filtrados`).
--
-- "Decisor provável" = sócio cuja qualificação indica cargo de administração/
-- direção (ex.: Administrador, Sócio-Administrador, Diretor, Presidente).
-- Quando nenhum sócio tem essas qualificações, cai para o primeiro sócio
-- pessoa física cadastrado.

DECLARE qualificacoes_decisor ARRAY<STRING> DEFAULT [
  '10', -- Diretor
  '16', -- Presidente
  '22', -- Sócio-Administrador
  '49', -- Sócio-Administrador (variação de tabela)
  '65'  -- Administrador
];

WITH leads AS (
  SELECT cnpj FROM `meu_projeto.prospeccao.leads_filtrados`
),

socios_ranqueados AS (
  SELECT
    s.cnpj_basico,
    s.nome_socio_razao_social,
    s.cnpj_cpf_socio,
    s.qualificacao_socio,
    s.faixa_etaria,
    s.data_entrada_sociedade,
    CASE WHEN s.qualificacao_socio IN UNNEST(qualificacoes_decisor) THEN 1 ELSE 0 END AS eh_decisor,
    ROW_NUMBER() OVER (
      PARTITION BY s.cnpj_basico
      ORDER BY
        CASE WHEN s.qualificacao_socio IN UNNEST(qualificacoes_decisor) THEN 0 ELSE 1 END,
        s.data_entrada_sociedade ASC
    ) AS ordem
  FROM `basedosdados.br_me_cnpj.socios` AS s
  WHERE SUBSTR(s.cnpj_basico, 1, 8) IN (
    SELECT SUBSTR(cnpj, 1, 8) FROM leads
  )
)

SELECT
  cnpj_basico,
  nome_socio_razao_social AS nome_decisor,
  cnpj_cpf_socio AS documento_decisor,
  qualificacao_socio,
  eh_decisor,
  data_entrada_sociedade
FROM socios_ranqueados
WHERE ordem = 1;
