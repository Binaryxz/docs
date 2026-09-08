-- Etapa 2 (parte 3): junta o resultado de filtro_cnae.sql com o decisor
-- provável de socios_decisor.sql, gerando a tabela "leads_com_socios" que
-- alimenta a etapa 3 (enrich_leads.py / agentic_enrich.py) e, no final,
-- build_planilha_final.py.
--
-- Rode depois de salvar os resultados de filtro_cnae.sql e
-- socios_decisor.sql em tabelas (ex.: `meu_projeto.prospeccao.leads_filtrados`
-- e `meu_projeto.prospeccao.socios_decisor`).
--
-- LEFT JOIN porque nem todo CNPJ tem um sócio com qualificação de
-- administração/direção cadastrado na base pública — esses leads continuam
-- na lista, só sem decisor identificado.

SELECT
  l.cnpj,
  l.razao_social,
  l.nome_fantasia,
  l.percentual_match_cnae,
  l.qtd_cnaes_alvo_encontrados,
  l.cnae_fiscal_principal,
  l.cnae_fiscal_secundaria,
  l.data_inicio_atividade,
  l.uf,
  l.municipio,
  l.ddd_1,
  l.telefone_1,
  l.ddd_2,
  l.telefone_2,
  l.correio_eletronico,
  s.nome_decisor,
  s.documento_decisor,
  s.qualificacao_socio,
  s.eh_decisor,
  s.data_entrada_sociedade
FROM `meu_projeto.prospeccao.leads_filtrados` AS l
LEFT JOIN `meu_projeto.prospeccao.socios_decisor` AS s
  ON s.cnpj_basico = SUBSTR(l.cnpj, 1, 8)
ORDER BY l.percentual_match_cnae DESC;
