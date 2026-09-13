-- As tabelas de `basedosdados.br_me_cnpj` (estabelecimentos, empresas,
-- socios) são snapshots mensais completos, particionados por `data` e
-- clusterizados por `ano,mes`. Rode esta query ANTES de filtro_cnae.sql /
-- socios_decisor.sql pra descobrir o período mais recente disponível, e
-- preencha ano_referencia/mes_referencia nesses scripts com o resultado.
--
-- Sem esse filtro, uma query nessas tabelas escaneia TODO o histórico
-- (centenas de GB, bilhões de linhas) em vez de só o snapshot atual.

SELECT ano, mes
FROM `basedosdados.br_me_cnpj.estabelecimentos`
GROUP BY ano, mes
ORDER BY ano DESC, mes DESC
LIMIT 1;
