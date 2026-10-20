# 🛡️ Auditoria Web3 — pipeline único

Cole o link de um repositório do GitHub e o pipeline faz a auditoria completa:
contratos, circuitos ZK, bridges, privacidade, zkTLS, governança e infraestrutura
off-chain. Ao final sai um `RELATORIO.md` consolidado.

```bash
./auditar.sh https://github.com/org/protocolo
```

## Como usar

### 1. GitHub Actions (recomendado, nada para instalar)

1. Cadastre os segredos em **Settings → Secrets and variables → Actions**:
   `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` e, se for usar, `ETHSCAN_API_KEY`,
   `ANTICAPTURE_MCP_API_KEY` e `AUDIT_GITHUB_TOKEN` (repositórios privados).
2. **Actions → Auditoria Web3 → Run workflow** → cole o link.
3. O relatório aparece no resumo da execução, e todos os arquivos ficam no
   artefato `auditoria-web3`.

### 2. Docker (local)

```bash
docker build -t auditoria-web3 auditoria-web3/
cp auditoria-web3/config.example.env config.env   # preencha as chaves
docker run --rm --env-file config.env -v "$PWD/resultados:/resultados" \
  auditoria-web3 https://github.com/org/protocolo --saida /resultados/protocolo
```

### 3. Direto na máquina

Precisa de `git`, `jq` e `python3`. Etapas cujas ferramentas não estiverem
instaladas são **puladas** (e aparecem como tal no relatório), nunca quebram o pipeline.

```bash
./auditar.sh https://github.com/org/protocolo --config config.env
./auditar.sh https://github.com/org/protocolo --etapas miesc,medusa --ref v2.0
./auditar.sh ./caminho/local                       # também aceita pasta local
./auditar.sh --listar                              # lista as etapas
```

## O que roda

O pipeline primeiro faz um **inventário** do repositório e só roda cada etapa
quando ela se aplica (ex.: Picus só roda se houver circuitos Circom).

| Etapa | Ferramenta | Roda quando | Precisa de |
|---|---|---|---|
| `miesc` | [MIESC](https://github.com/fboiero/MIESC) (Slither, Mythril, +35) | há `.sol` | — |
| `solidityguard` | [SolidityGuard](https://github.com/alt-research2/SolidityGuard) | há `.sol` | — |
| `symgpt` | [SymGPT](https://github.com/symbolic-gpt/symgpt) | há contratos ERC | `OPENAI_API_KEY` |
| `medusa` | [Medusa](https://github.com/crytic/medusa) | Foundry/Hardhat + funções `property_*`/`invariant_*` | — |
| `picus` | [Picus](https://github.com/Veridise/Picus) | há `component main` em `.circom` | — |
| `circheck` | `circom --inspect` (+ `circheck`, se instalado) | há `component main` em `.circom` | — |
| `bridgeguard` | [BridgeGuard MCP](https://www.npmjs.com/package/bridgeguard-mcp) | código de bridge/cross-chain/Hyperliquid | `ANTHROPIC_API_KEY` |
| `privacidade` | privacy-protocol-auditor | nullifiers, shielded pools, mixers | `ANTHROPIC_API_KEY` |
| `zktls` | TLSNotary / Reclaim Protocol | uso de `tlsn`, `@reclaimprotocol`, attestor-core | `ANTHROPIC_API_KEY` |
| `governanca` | [Anticapture](https://github.com/blockful/anticapture) | Governor/Timelock/ERC20Votes | `ANTHROPIC_API_KEY` (+ `ANTICAPTURE_MCP_API_KEY` para dados on-chain) |
| `quorum` | [Quorum (Certora)](https://github.com/Certora/Quorum) | `QUORUM_PAYLOADS` definido | `ETHSCAN_API_KEY` |
| `nodeprobe` | [Nodeprobe](https://github.com/ehsanhajian/nodeprobe) | `NODEPROBE_WEB`/`_RPC`/`_CONTRATOS` definidos | — |
| `implantacao` | leitura on-chain (web3.py) | `ONCHAIN_ENDERECOS` e `ONCHAIN_RPC` definidos | — |

## Resultado

```
resultados/<repo>-<data>/
├── RELATORIO.md          ← relatório consolidado
├── resumo.json           ← o mesmo, em JSON (para CI/dashboards)
├── inventario/           ← o que foi encontrado no repositório
└── etapas/<etapa>/       ← saída bruta de cada ferramenta + status.json
```

## Relatório para o cliente (`AUDITORIA.md`)

Toda execução também gera `AUDITORIA.md`, um rascunho no formato das grandes empresas de
auditoria (Trail of Bits, OpenZeppelin, Spearbit, Zellic): capa, aviso legal, resumo executivo,
escopo com hash de cada arquivo, metodologia, matriz impacto × probabilidade, achados com IDs
(`H-01`, `M-02`...), riscos de centralização e apêndices.

O pipeline preenche sozinho o escopo, a metodologia, a cobertura por ferramenta e os sinais
brutos (Apêndice A). **Os achados confirmados vêm da triagem**, que é trabalho do auditor:

1. Copie `modelo/triagem.exemplo.json`, registre cada achado confirmado (impacto, probabilidade,
   cenário de exploração, recomendação) e os dados do cliente.
2. Gere de novo:
   ```bash
   python3 relatorio_profissional.py resultados/<repo>-<data> --triagem triagem.json
   ```
3. Depois da correção pelo cliente, preencha `status`, `resposta_cliente` e `revisao_correcao`,
   suba a `versao` e gere de novo.

A severidade é calculada pela matriz quando você informa `impacto` e `probabilidade`, ou pode ser
fixada com `severidade`.

## Limitações, sem enfeite

- **Achados são brutos.** Ferramentas automáticas geram falsos positivos; tudo
  precisa de triagem humana. Isto **não substitui** uma auditoria manual.
- **Medusa precisa de invariantes.** Sem funções `property_*`/`invariant_*`
  (ou `MEDUSA_ALVOS`), a etapa é pulada: fuzzing sem propriedade não prova nada.
- **Circheck:** o repositório `zksecurity/circheck` indicado não existe
  publicamente. A etapa usa `circom --inspect`, que detecta sinais sub-restritos,
  e roda `circheck` só se você tiver o binário no `PATH`.
- **privacy-protocol-auditor** é uma skill de agente, não um CLI. A etapa aplica
  a mesma checklist via Claude Code; se você baixar o `SKILL.md` original,
  aponte `PRIVACY_SKILL_PATH` para ele, e o texto entra no prompt.
- **TLSNotary e Reclaim** são protocolos de prova, não scanners. A etapa audita
  *a integração* (verificação da prova, binding ao pedido, replay, campos
  extraídos, vazamento de chaves). O repositório `tlsnotary/tlsnotary` é legado;
  a implementação atual é `tlsnotary/tlsn`.
- **Quorum e Anticapture** analisam coisas **deployadas** (payloads de proposta,
  DAOs on-chain). Sem endereços configurados, a governança é revisada só pelo código.
- **Nodeprobe não escaneia URLs achadas no código**, só as que você configurar.
  Escaneie apenas infraestrutura que você opera ou tem autorização para testar.

## Segurança do próprio pipeline

O repositório auditado é tratado como **não confiável**:

- dependências npm instaladas com `--ignore-scripts`;
- as revisões por LLM só podem ler arquivos (`Read`, `Grep`, `Glob`); shell,
  escrita e rede ficam bloqueados, e o prompt manda ignorar instruções do código;
- chaves de API nunca são gravadas na pasta de resultados;
- no CI, tudo roda dentro de um container descartável.

Mesmo assim, compilar um projeto Foundry/Hardhat pode executar código dele.
Audite repositórios desconhecidos só no container ou no CI.
