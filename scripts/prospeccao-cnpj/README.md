# Pipeline de prospecção via CNPJ + IA

Pipeline genérico (config-driven) para encontrar empresas-alvo no Brasil a
partir da base pública de CNPJ, identificar o decisor provável, enriquecer
com dados públicos da web e exportar uma planilha final de leads. Adaptado
de uma sessão anterior focada em fintechs — o essencial do pipeline não
mudou, só o que era específico daquele cliente (lista de CNAEs, palavras-
chave) foi extraído para `config.py`.

## Visão geral (5 etapas)

1. **Filtro por CNAE** — `filtro_cnae.sql` (BigQuery) ou
   `filtrar_cnpjs_local.py` (sem BigQuery). Peso 2 = CNAE principal/core do
   ICP, peso 1 = auxiliar/genérico.
2. **Filtro por capital social / porte** — teto de capital social (ex.:
   ≤ R$5M), já embutido nas etapas acima.
3. **Decisor provável** — cruza com a base de sócios, prioriza qualificação
   de administração/direção. `socios_decisor.sql` (BigQuery, já embutido
   se você usar `filtrar_cnpjs_local.py`).
4. **(Opcional, pago) Verificação de contato via Sherlocker** —
   `verificar_telefone_decisor.py`.
5. **(Opcional) Agente de IA com busca na web** — `enrich_leads.py`
   (Gemini + grounding), `agentic_enrich.py` (Gemini + Brave Search,
   função ativa) ou `enrich_leads_gratis.py` (só Brave Search, sem LLM).

Depois: `build_planilha_final.py` junta tudo numa planilha, e
`adaptar_formato_importacao.py` (opcional) reformata pra outra ferramenta.

## Passo a passo

```bash
# 0. Configure o ICP deste cliente
cp config.example.py config.py
$EDITOR config.py   # CNAES_ALVO, LIMIAR_PERCENTUAL, CAPITAL_SOCIAL_MAX, ...

pip install -r requirements.txt
```

### Caminho A — com BigQuery (recomendado para a base nacional completa)

1. Rode `descobrir_periodo.sql` no console/`bq` do BigQuery e anote
   `ano`/`mes` mais recentes.
2. Copie os mesmos valores de `CNAES_ALVO`/`LIMIAR_PERCENTUAL`/
   `CAPITAL_SOCIAL_MAX`/`PORTES_ALVO`/`PALAVRAS_CHAVE_NOME` de `config.py`
   para o bloco `CONFIGURAÇÃO` no topo de `filtro_cnae.sql` (o BigQuery não
   importa Python, por isso a duplicação), junto com o período do passo 1.
3. Rode `filtro_cnae.sql`, salve o resultado em
   `meu_projeto.prospeccao.leads_filtrados`.
4. Ajuste `ano_referencia`/`mes_referencia` e a lista de
   `qualificacoes_decisor` em `socios_decisor.sql` (mesmos valores de
   `QUALIFICACOES_DECISOR` em `config.py`), rode, salve em
   `meu_projeto.prospeccao.socios_decisor`.
5. Rode `join_leads_socios.sql`, exporte o resultado como
   `leads_com_socios.csv`.

Ver `../../guides/prospeccao-cnpj.mdx` para o setup completo de GCP/BigQuery
(roles de service account, cota gratuita, etc.) e as armadilhas de schema já
mapeadas (snapshots mensais, nomes de coluna, CPF mascarado).

### Caminho B — sem BigQuery (arquivos abertos da Receita Federal)

```bash
python filtrar_cnpjs_local.py \
  --estabelecimentos "dados/*ESTABELE*" \
  --empresas "dados/*EMPRECSV*" \
  --socios "dados/*SOCIOCSV*" \
  --saida leads_com_socios.csv
```

### Etapas seguintes (comuns aos dois caminhos)

```bash
# 4. (opcional, pago) confirmar titular do telefone
export SHERLOCKER_API_KEY="..."
python verificar_telefone_decisor.py leads_com_socios.csv leads_com_telefone_decisor.csv

# 5. enriquecimento por IA — escolha uma opção
export GEMINI_API_KEY="..."
python enrich_leads.py leads_com_socios.csv leads_enriquecidos.csv
# ou: export BRAVE_API_KEY="..."; python agentic_enrich.py ...
# ou (sem LLM, mais barato):        python enrich_leads_gratis.py ...

# 6. planilha final
python build_planilha_final.py leads_com_socios.csv leads_enriquecidos.csv planilha_final.xlsx

# 7. (opcional) formato de importação de outra ferramenta
python adaptar_formato_importacao.py leads_com_socios.csv leads_enriquecidos.csv saida_importacao.csv
```

## Preferências deste cliente (contadores)

- **O entregável final é sempre `adaptar_formato_importacao.py`** — exatamente
  as 6 colunas `nome, primeiro_nome, telefone, email, regiao, valor`, nunca
  colunas extras. Se precisar de uma coluna informativa (ex.: indicador de
  WhatsApp confirmado) pra revisar antes de entregar, gere um arquivo
  separado — não altere o formato do arquivo final.
- **WhatsApp é o canal que importa, não LinkedIn.** A coluna `telefone` do
  entregável final prioriza `whatsapp_publico` sobre um telefone comercial
  genérico (`telefone_comercial_ia`) — ver ordem em
  `adaptar_formato_importacao.py`.

## Lições já incorporadas (não repetir)

- **Schema do `basedosdados.br_me_cnpj`**: tabelas são snapshots mensais —
  sempre filtre `ano`/`mes` (ver `descobrir_periodo.sql`), `situacao_cadastral`
  é `'2'` sem zero à esquerda, e as colunas certas são `email`/`sigla_uf`/
  `id_municipio` (não `correio_eletronico`/`uf`/`municipio`). Isso só vale
  para o Caminho A (BigQuery) — os arquivos brutos da Receita usados no
  Caminho B (`filtrar_cnpjs_local.py`) têm layout diferente (`situacao_cadastral`
  com zero à esquerda, `correio_eletronico`/`uf`/`municipio` como nomes
  corretos nesse layout).
- **CPF mascarado**: a Receita mascara o CPF de sócio pessoa física
  (`***257278**`). `verificar_telefone_decisor.py` compara por NOME
  normalizado como critério principal por causa disso, não por CPF exato.
- **Sherlocker**: use a API REST direta (não o MCP) para lote; 60 req/min;
  é uma exceção deliberada à regra de não usar data brokers — trate a saída
  como dado pessoal sensível (LGPD).
- **Brave/LinkedIn**: não dá pra extrair headcount real por busca simples
  (o snippet mostra seguidores, não funcionários).
- **Nunca commitar credenciais** — chave de service account do GCP,
  `SHERLOCKER_API_KEY`, `GEMINI_API_KEY`, `BRAVE_API_KEY` sempre por
  variável de ambiente, nunca no código ou versionadas.

Detalhes completos (setup de GCP passo a passo, roles necessárias, etc.) em
`../../guides/prospeccao-cnpj.mdx`.
