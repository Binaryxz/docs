# Deploy do Módulo 1 – Vendas na Run on Flux

Este diretório contém os artefatos para publicar a stack descrita em
[`/modulo-vendas/arquitetura.mdx`](../modulo-vendas/arquitetura.mdx)
(OLMo 3 32B + Conway Automaton + n8n) como containers Docker, para você
rodar em uma instância alugada em **cloud.runonflux.com**.

> **Nota de transparência:** a partir deste ambiente não consigo acessar
> `cloud.runonflux.com` (bloqueado pelo proxy de rede da sessão), então não
> pude confirmar a UI atual de criação de instância nem os planos/preços
> exatos. O passo 2 abaixo descreve o fluxo esperado de um marketplace de
> instâncias GPU — confirme os nomes de tela reais no seu dashboard da Flux
> antes de seguir à risca.

Eu não tenho carteira Flux nem credenciais de Chatwoot/WhatsApp/Meta
Ads/Google Ads, então não consigo executar o deploy por você — os passos
abaixo são para você (ou alguém da equipe) rodar.

## 1. Pré-requisitos

- Conta em [cloud.runonflux.com](https://cloud.runonflux.com/) com saldo
  para alugar uma instância GPU.
- Um domínio (ou subdomínio) apontável para o IP da instância, ex.:
  `vendas.seudominio.com`.
- Chave SSH própria.
- Tokens/credenciais de: Chatwoot (BR e EU), WhatsApp Business Cloud API
  (BR e EU), Meta Ads, Google Ads, e-mail (IMAP/SMTP).
- Acesso ao Hugging Face para baixar os pesos do OLMo 3 32B, se o modelo
  exigir aceite de licença/token.

## 2. Alugar a instância GPU

O OLMo 3 32B em fp16 precisa de bastante VRAM (por volta de 64GB só para
os pesos, mais espaço para contexto/KV-cache). Duas opções:

- **Sem quantização:** instância com GPU(s) somando 80GB+ de VRAM (ex.:
  1x A100/H100 80GB, ou 2x GPUs de 48GB com `--tensor-parallel-size 2` no
  vLLM).
- **Quantizado (AWQ/GPTQ/INT4):** cabe em uma única GPU de 24–48GB; troque
  a imagem/flags do serviço `olmo` em `docker-compose.yml` por uma variante
  quantizada do modelo.

No painel da Flux Cloud, procure por algo como "Deploy Instance" /
"Compute" → escolha um plano com GPU, sistema operacional Ubuntu LTS, e
associe sua chave SSH. Anote o IP público atribuído.

## 3. Apontar o DNS

Crie um registro `A` para `vendas.seudominio.com` (ou o domínio escolhido)
apontando para o IP público da instância. O Caddy deste stack solicita
certificado TLS automaticamente (Let's Encrypt) assim que o domínio
resolver para a instância.

## 4. Preparar a instância

```bash
ssh root@SEU_IP

# Docker + Docker Compose plugin
curl -fsSL https://get.docker.com | sh

# NVIDIA Container Toolkit (necessário para o serviço `olmo` usar a GPU)
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/libnvidia-container/gpgkey | apt-key add -
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list \
  | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update && apt-get install -y nvidia-container-toolkit
systemctl restart docker
```

## 5. Clonar o repositório e configurar

```bash
git clone https://github.com/Binaryxz/docs.git
cd docs/deploy
cp .env.example .env
nano .env   # preencha domínio, tokens do Chatwoot/WhatsApp/Ads/e-mail
```

Antes de subir em produção, siga o checklist de
[`automaton/README.md`](automaton/README.md) — o Conway Automaton vem por
padrão com carteira própria, gasto on-chain e auto-replicação, capacidades
que precisam ser explicitamente travadas para este caso de uso.

## 6. Subir a stack

```bash
docker compose up -d
docker compose logs -f olmo      # acompanhe o download/carregamento do modelo
```

Verifique se o LLM respondeu:

```bash
curl http://localhost:8000/v1/models -H "Authorization: Bearer $OLMO_API_KEY"
```

## 7. Configurar o n8n

1. Acesse `https://vendas.seudominio.com/n8n/` e faça login com
   `N8N_BASIC_AUTH_USER` / `N8N_BASIC_AUTH_PASSWORD`.
2. Cadastre as credenciais reais: Chatwoot BR/EU (header auth com o token
   da API), WhatsApp Cloud API BR/EU, SMTP.
3. Importe os workflows de exemplo em `n8n/workflows/`:
   - `sla-monitor-chatwoot.json` — varre conversas abertas e alerta SLA >1h.
   - `approval-callback.json` — recebe sugestões do agente, aguarda decisão
     humana, envia (WhatsApp/e-mail) só após aprovação e registra o
     resultado.
4. Ajuste os nós de HTTP Request/WhatsApp/E-mail para usar as credenciais
   cadastradas (eles vêm com placeholders).
5. Ative os dois workflows.

Esses dois workflows cobrem o núcleo do loop (monitorar → sugerir →
aprovar → enviar → registrar). As demais funcionalidades descritas em
[`/modulo-vendas/funcionalidades.mdx`](../modulo-vendas/funcionalidades.mdx)
(correlação com Meta/Google Ads, avaliação de influencers, análise de
outbound) seguem o mesmo padrão — leitura via HTTP Request, análise via
`http://automaton:3000`, aprovação via os mesmos webhooks — e ficam para
workflows adicionais conforme a equipe for confirmando os detalhes de cada
integração (APIs de anúncios, planilha/CRM de outbound etc.).

## 8. Teste de ponta a ponta

1. Abra uma conversa de teste no Chatwoot e deixe passar 1h sem responder.
2. Confirme que `sla-monitor-chatwoot` disparou o alerta com sugestão.
3. Aprove (ou edite/rejeite) pelo canal configurado.
4. Confirme que a mensagem chegou ao cliente de teste somente após a
   aprovação, e que o resultado foi registrado no Chatwoot.

## 9. Operação

- **Logs:** `docker compose logs -f <serviço>`.
- **Atualizar:** `git pull && docker compose up -d --build`.
- **Backup:** o volume `n8n_data` guarda os workflows/credenciais do n8n;
  faça backup periódico dele.
- **Custos:** monitore o uso de GPU (a maior parte do custo da instância)
  e, se o Automaton mantiver alguma carteira on-chain, monitore o saldo
  dela separadamente.
