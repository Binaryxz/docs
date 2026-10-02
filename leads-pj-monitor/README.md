# Leads PJ Monitor

Sistema que monitora processos judiciais recém-distribuídos contra empresas
no Brasil, cruza com dados públicos de CNPJ e dívida ativa, filtra empresas
pelo perfil que você configurar, e organiza tudo numa tela — sem precisar
programar nada depois de instalado.

**Status desta versão (Fase 1 do projeto):** coleta de processos (DataJud),
importação da Receita Federal e da PGFN, casamento com CNPJ, filtros e
pontuação de leads, painel e tela de leads. **Ainda não incluído** (fases
seguintes): raspagem de partes diretamente nos sites dos tribunais quando o
DataJud não traz isso pronto (Fase 2), enriquecimento de telefone/e-mail via
FonteData/BigDataCorp e exportação no CSV de campanha (Fase 3).

---

## 1. O que você precisa antes de instalar

- Um computador Linux (ou um Mac/Windows com Docker Desktop) **ou** uma VPS
  Linux barata (2 vCPUs, 4 GB de RAM já funciona para a Fase 1; se for
  importar a base completa da Receita Federal depois, reserve **disco
  generoso — pelo menos 100 GB livres**, porque os dados da Receita e da
  PGFN juntos passam de 50 GB).
- [Docker e Docker Compose instalados](https://docs.docker.com/get-docker/)
  — no Linux, normalmente `sudo apt install docker.io docker-compose-plugin`.
- Uma chave de acesso à API Pública do DataJud. É gratuita: peça em
  https://datajud-wiki.cnj.jus.br/ (procure por "Credenciais da API Pública").

Não precisa saber programar. Os passos abaixo são só comandos pra copiar e
colar no terminal.

---

## 2. Instalação (resumo: 4 comandos)

```bash
git clone <endereço-deste-repositório>
cd leads-pj-monitor
cp .env.example .env
```

Agora abra o arquivo `.env` num editor de texto qualquer e preencha:

- `POSTGRES_PASSWORD` e `APP_LOGIN_PASSWORD`: escolha senhas suas (qualquer
  coisa, só não deixe em branco).
- `APP_SECRET_KEY`: gere uma rodando este comando e colando o resultado:
  ```bash
  docker run --rm python:3.11-slim python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- `DATAJUD_API_KEY`: a chave que você pegou no passo anterior.

Salve o arquivo e suba o sistema:

```bash
docker compose up -d
```

A primeira vez demora alguns minutos (baixando as imagens). Depois disso,
abra `http://localhost:8501` no navegador (ou `http://<ip-da-sua-vps>:8501`
se estiver numa VPS) e entre com a senha que você colocou em
`APP_LOGIN_PASSWORD`.

Pra parar o sistema: `docker compose down`. Pra atualizar depois de baixar
uma versão nova do código: `docker compose up -d --build`.

---

## 3. Primeiros passos dentro do sistema

1. **Configurações → Coleta**: escolha os tribunais (comece com 1 ou 2
   estados, não todos de uma vez) e as classes processuais (já vem com
   Execução Fiscal, Cumprimento de Sentença e Execução de Título
   Extrajudicial marcadas — é o perfil mais comum pra esse tipo de
   prospecção).
2. **Painel → "Rodar agora"**: dispara a primeira coleta. Ela roda em
   segundo plano; atualize a página depois de um tempo para ver o resultado
   em "Últimas execuções".
3. **Configurações → Sistema → "Importar Receita Federal agora"**: sem
   isso, nenhum processo vira lead (é a Receita que dá o CNPJ, telefone,
   situação da empresa etc.). **Isso demora horas e usa bastante disco** —
   rode fora do horário de pico e confira o espaço em disco antes. Depois
   da primeira vez, roda só automaticamente, todo dia 1 do mês.
4. **Configurações → Sistema → "Importar PGFN agora"**: opcional pra
   começar, mas melhora muito o lead score (dívida ativa é um sinal forte).
   Roda automaticamente a cada 3 meses depois da primeira vez.
5. Volte no **Painel** e clique "Rodar agora" de novo — agora com a Receita
   importada, os processos coletados vão virar leads de verdade na aba
   **Leads**.
6. Na aba **Leads**, ajuste os filtros e salve como "nicho" pra não
   precisar reconfigurar todo dia.

Qualquer parâmetro (tribunais, filtros de empresa grande, pesos do score,
horário da coleta diária) é ajustável na aba **Configurações** — nunca é
necessário editar código ou reiniciar o container pra isso (o horário da
coleta, por exemplo, já reagenda sozinho ao salvar).

---

## 4. Entendendo as telas

- **Painel**: visão geral — quantos leads novos, gasto do mês (quando o
  enriquecimento entrar, Fase 3), status das últimas coletas, e um alerta
  de atraso por tribunal (tempo entre o processo ser ajuizado e aparecer
  aqui — se isso disparar, pode ser que o DataJud esteja atrasado pra
  aquele tribunal específico, o que é normal variar).
- **Leads**: a lista principal, com todos os filtros combináveis. Clique
  num lead pra ver detalhe (sócios, dívida PGFN) e mudar o status.
- **Revisão manual**: casos em que o nome da empresa no processo não bateu
  com confiança suficiente contra nenhum CNPJ da Receita — você decide se
  aceita a sugestão ou descarta.
- **Configurações**: todos os parâmetros do sistema.
- **Conformidade**: de onde vêm os dados, lista de opt-out (bloqueia um
  CNPJ/CPF em todo o sistema), exclusão de dados de contato, e log de
  auditoria.

---

## 5. Como funciona por dentro (resumo técnico, pra quem for dar manutenção)

```
DataJud (CNJ) ──► processos / partes ──┐
                                        ├──► matching (CNPJ exato ou nome
Receita Federal (dados abertos) ──► empresas_receita   fuzzy, rapidfuzz) ──► leads
PGFN (dados abertos) ──► pgfn_divida_ativa ─────────────────┘
                                                              │
                                                              ▼
                                                    qualificação (filtros +
                                                      score 0-100) ──► tela
                                                                        de Leads
```

- **Banco**: Postgres, schema em `migrations/*.sql`, aplicado automaticamente
  no `docker compose up` (ver `scripts/aplicar_migracoes.py`).
- **Coleta (Módulo 1)**: `app/datajud_client.py`. Pagina pelo `search_after`
  do Elasticsearch, grava cursor em `config_kv` pra retomar sem reprocessar.
  Os nomes exatos de alguns subcampos do DataJud podem variar entre
  tribunais — tudo é lido de forma defensiva e o hit bruto fica salvo em
  `processos.bruto_json`, então nada se perde se algum campo precisar de
  ajuste depois.
- **Importação Receita/PGFN (Módulo 3)**: `app/receita_import.py` e
  `app/pgfn_import.py`. Usa `COPY` do Postgres (bulk load), não insert
  linha a linha — testado com os arquivos reais chega a ~235 mil linhas/seg.
  Atenção: os 10 arquivos de cada dataset da Receita são particionados de
  forma independente entre si — nunca junte por índice de arquivo, sempre
  acumule todos antes do JOIN final (é o que o código já faz).
- **Matching**: `app/matching.py`. CNPJ exato primeiro (com validação de
  dígito verificador); fuzzy por UF (nunca carrega a base nacional inteira
  em memória — isso é importante, a tabela pode ter mais de 50 milhões de linhas).
- **Qualificação**: `app/qualification.py`. Score por percentil dentro do
  lote (não por teto fixo). Exclusão de marca grande: lista em
  `marcas_grandes_exclusao`, editável pela tela — já vem com ~230 termos
  de partida, herdados e corrigidos de um projeto anterior de prospecção
  parecido (vários ajustes de falso positivo, tipo não deixar "Pacheco"
  sozinho bloquear qualquer sobrenome comum).
- **Agendador**: `app/scheduler.py`, APScheduler em processo (sem precisar
  de Celery/Redis — mais simples de instalar numa VPS pequena).

### Rodando os testes

```bash
docker compose exec app pip install -r requirements-dev.txt
docker compose exec app pytest
```

Os testes de `test_matching.py` e `test_datajud_client.py` são puros (não
tocam banco). Os de `test_qualification_integration.py` usam o Postgres
real do container — por isso só fazem sentido rodando assim, dentro dele.

---

## 6. Limitações conhecidas desta versão (Fase 1)

- A TPU (classes/assuntos) não é raspada em tempo real do site antigo do
  CNJ (é frágil e sem API estável) — usa uma lista inicial pequena mais o
  que o próprio DataJud já trouxe nos processos coletados. Cresce com o
  uso.
- O layout dos arquivos da Receita Federal foi validado contra os arquivos
  reais (Empresas, Municípios, Qualificações); o arquivo de
  Estabelecimentos (o maior, ~20 GB) usa o layout oficial documentado pela
  Receita mas não foi baixado por completo durante o desenvolvimento por
  tamanho — confirme com uma importação pequena antes de confiar
  cegamente na automática.
- Sem raspagem própria por tribunal ainda (Módulo 2): o campo de partes só
  vem preenchido quando o próprio DataJud já trouxer isso no retorno
  (varia por tribunal).
- Sem enriquecimento de telefone/e-mail de sócio por provedor pago, e sem
  o botão de exportação no CSV de campanha — ambos chegam na Fase 3.
