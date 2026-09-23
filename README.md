## 🔎 Pipeline de prospecção via CNPJ + IA

Pipeline config-driven que filtra empresas brasileiras reais (dados oficiais
da Receita Federal) por CNAE/capital social, identifica o decisor provável
de cada uma e enriquece com dados públicos da web (site, WhatsApp, LinkedIn)
— gerando uma planilha pronta pra disparo comercial.

- 📖 **[Guia completo](prospeccao-cnpj/guide.mdx)** — arquitetura, setup de
  GCP/BigQuery, passo a passo de cada etapa.
- ⚙️ **[Scripts e config](prospeccao-cnpj/scripts/)** — SQL, filtro local,
  enriquecimento por IA, formatação final. Leia
  **[`prospeccao-cnpj/README.md`](prospeccao-cnpj/README.md)** antes de rodar.

## 🎯 Prospecção Fintech

Pipeline completo de geração de leads B2B para empresas fintech-adjacentes no Brasil: filtro por CNAE/capital social na base de CNPJ da Receita Federal, identificação do decisor (sócio-administrador), validação de telefone (formato + WhatsApp ativo) e enriquecimento via IA.

- 📖 **[Guia completo](prospeccao-fintech/guia.mdx)**
- 💻 **[Scripts](prospeccao-fintech/scripts)**

---

# Mintlify Starter Kit

Click on `Use this template` to copy the Mintlify starter kit. The starter kit contains examples including

- Guide pages
- Navigation
- Customizations
- API Reference pages
- Use of popular components

### Development

Install the [Mintlify CLI](https://www.npmjs.com/package/mintlify) to preview the documentation changes locally. To install, use the following command

```
npm i -g mintlify
```

Run the following command at the root of your documentation (where mint.json is)

```
mintlify dev
```

### Publishing Changes

Install our Github App to auto propagate changes from your repo to your deployment. Changes will be deployed to production automatically after pushing to the default branch. Find the link to install on your dashboard. 

#### Troubleshooting

- Mintlify dev isn't running - Run `mintlify install` it'll re-install dependencies.
- Page loads as a 404 - Make sure you are running in a folder with `mint.json`
