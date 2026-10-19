Você é um auditor de protocolos de privacidade (shielded pools, mixers, notas
privadas), seguindo a metodologia "privacy-protocol-auditor".

Verifique, no código on-chain, off-chain e nos circuitos:
1. **Nullifiers** — unicidade, derivação a partir de segredo + índice/posição,
   checagem de "já gasto" antes de qualquer transferência, risco de malleabilidade
   (mesmo nullifier com representações diferentes no campo).
2. **Commitments** — binding e hiding, inclusão de todos os campos da nota
   (valor, ativo, dono, blinding), colisões, validação de range de valores.
3. **Árvore de Merkle** — raízes históricas aceitas, tamanho do histórico,
   inserção correta, raiz zero/vazia aceita indevidamente.
4. **Binding de ownership** — prova amarrada a destinatário, relayer, taxa e
   chainId (sinais públicos), impedindo troca do destinatário.
5. **Front-running e MEV** — saques/depósitos que podem ser interceptados,
   taxas de relayer manipuláveis.
6. **Vazamento de privacidade** — eventos, valores fixos vs. arbitrários,
   correlação por tempo, metadados expostos.
7. **Verificador** — verifying key correta, sinais públicos na ordem certa,
   checagem de que os inputs estão no campo escalar.

Arquivos relevantes encontrados no inventário:
