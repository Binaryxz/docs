-- Etapa 3 do pipeline: sócios e decisor provável de cada CNPJ já filtrado.
-- Rode depois de salvar o resultado de filtro_cnae.sql em uma tabela
-- (ex.: `meu_projeto.prospeccao.leads_filtrados`).
--
-- "Decisor provável" = sócio cuja qualificação indica cargo de administração/
-- direção (ex.: Administrador, Sócio-Administrador, Diretor, Presidente).
-- Quando nenhum sócio tem essas qualificações, cai para o sócio mais antigo
-- cadastrado.
--
-- Nomes de coluna nesta tabela (diferente do que "faria sentido" assumir,
-- validado em 2026-09): é `nome`/`documento`/`qualificacao`, não
-- `nome_socio_razao_social`/`cnpj_cpf_socio`/`qualificacao_socio`.
--
-- ATENÇÃO — CPF mascarado: a Receita mascara o CPF de sócio pessoa física
-- (ex.: `***257278**`, só 6 dos 11 dígitos visíveis). `documento_decisor`
-- abaixo sai mascarado assim sempre que o sócio for pessoa física — não dá
-- pra comparar com um CPF completo de outra fonte por igualdade exata (ver
-- verificar_telefone_decisor.py, que compara por nome normalizado em vez
-- de CPF por causa disso).
--
-- Ajuste ano_referencia/mes_referencia para os MESMOS valores usados em
-- filtro_cnae.sql (mesmo snapshot mensal).

DECLARE ano_referencia INT64 DEFAULT 2026;  -- <- mesmo valor de filtro_cnae.sql
DECLARE mes_referencia INT64 DEFAULT 1;     -- <- idem

DECLARE qualificacoes_decisor ARRAY<STRING> DEFAULT [
  '10', -- Diretor
  '16', -- Presidente
  '22', -- Sócio-Administrador
  '49', -- Sócio-Administrador (variação de tabela)
  '65'  -- Administrador
]; -- = QUALIFICACOES_DECISOR em config.py

WITH leads AS (
  SELECT cnpj FROM `meu_projeto.prospeccao.leads_filtrados`
),

socios_ranqueados AS (
  SELECT
    s.cnpj_basico,
    s.nome,
    s.documento,
    s.qualificacao,
    s.faixa_etaria,
    s.data_entrada_sociedade,
    CASE WHEN s.qualificacao IN UNNEST(qualificacoes_decisor) THEN 1 ELSE 0 END AS eh_decisor,
    ROW_NUMBER() OVER (
      PARTITION BY s.cnpj_basico
      ORDER BY
        CASE WHEN s.qualificacao IN UNNEST(qualificacoes_decisor) THEN 0 ELSE 1 END,
        s.data_entrada_sociedade ASC
    ) AS ordem
  FROM `basedosdados.br_me_cnpj.socios` AS s
  WHERE s.ano = ano_referencia
    AND s.mes = mes_referencia
    AND SUBSTR(s.cnpj_basico, 1, 8) IN (
      SELECT SUBSTR(cnpj, 1, 8) FROM leads
    )
)

SELECT
  cnpj_basico,
  nome AS nome_decisor,
  documento AS documento_decisor,
  qualificacao AS qualificacao_socio,
  eh_decisor,
  data_entrada_sociedade
FROM socios_ranqueados
WHERE ordem = 1;
