# Conway Automaton — checklist antes de rodar em produção

O [Conway Automaton](https://github.com/Conway-Research/automaton) é um
framework de **agente autônomo de propósito geral**: por padrão ele gera
sua própria carteira Ethereum, registra identidade on-chain (ERC-8004 na
Base), pode gastar créditos para se manter "vivo", se auto-modificar e se
auto-replicar. Nada disso é necessário — nem desejável — para o Módulo 1
(Vendas), cujo requisito central é **nenhuma mensagem sai sem aprovação
humana**.

Este diretório assume que a equipe optou por usar o Automaton mesmo assim.
Antes do primeiro deploy real, confirme cada item abaixo **na
documentação/config atual do projeto** (o repositório evolui; os nomes de
variável usados em `.env.example` e no `Dockerfile` são um ponto de
partida, não uma referência oficial):

## Checklist obrigatório

- [ ] **Carteira dedicada.** Gere uma carteira Ethereum nova, só para este
      agente, sem vínculo com contas pessoais ou corporativas existentes,
      e sem mais fundos do que o estritamente necessário para operar.
- [ ] **Auto-replicação desativada.** Confirme no config real do projeto
      qual flag desliga a criação de instâncias filhas, e defina-a.
- [ ] **Gasto on-chain autônomo desativado ou travado com teto rígido.**
      Se não houver como desativar totalmente, defina um teto de gasto
      muito baixo e monitore a carteira.
- [ ] **Auto-modificação de código desativada** ou, no mínimo, com todo
      diff auditado antes de ir para produção (o projeto descreve logs de
      auditoria para essa função — confirme onde eles ficam e monitore-os).
- [ ] **Ferramentas/skills restritas** ao necessário para o mandato
      (Chatwoot, WhatsApp, Meta/Google Ads, e-mail, webhook de aprovação
      do n8n). Remova acesso a shell, provisionamento de infraestrutura,
      gestão de domínios e qualquer capacidade "genérica" que o framework
      ofereça por padrão.
- [ ] **Genesis prompt / constituição substituídos** pelo conteúdo de
      `genesis-prompt.md` deste diretório (ou equivalente), deixando claro
      que aprovação humana é obrigatória para qualquer envio.
- [ ] **Saída sempre mediada pelo n8n.** O agente publica sugestões em
      `AUTOMATON_APPROVAL_WEBHOOK_URL`; ele não deve ter credenciais
      diretas de envio (token do WhatsApp, SMTP) — essas credenciais ficam
      só no n8n, que envia depois da aprovação.
- [ ] **Revisão de licença/custos.** Confirme o modelo de custo do Conway
      Cloud (ou de qualquer inferência externa que o Automaton use por
      padrão) — o objetivo é que ele use o endpoint local do OLMo 3 32B
      (`AUTOMATON_INFERENCE_BASE_URL`), não um provedor pago externo.

## O que este diretório fornece

- `Dockerfile` — clona e builda o Automaton a partir do upstream (fixe
  `AUTOMATON_REF` em um commit revisado, não em `main`).
- `genesis-prompt.md` — rascunho do mandato e das restrições rígidas do
  agente para o Módulo 1 – Vendas.

## O que falta e não pode ser adivinhado

Eu não tenho acesso à configuração completa do Automaton (só ao README
público). Antes de subir isso em produção, alguém da equipe precisa:

1. Rodar o projeto localmente uma vez (`automaton.sh` ou
   `npm run build && node dist/index.js --run`) e mapear os nomes reais
   das variáveis de ambiente/flags de config para cada item do checklist
   acima.
2. Confirmar se existe uma forma suportada de apontar `inference` para um
   endpoint OpenAI-compatible próprio (o OLMo servido via vLLM neste
   docker-compose) em vez do(s) modelo(s) padrão do Conway Cloud.
3. Ajustar `docker-compose.yml`/`.env.example` deste repositório com os
   nomes corretos assim que confirmados.
