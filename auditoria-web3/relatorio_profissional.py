#!/usr/bin/env python3
"""Gera AUDITORIA.md: o relatório de auditoria no formato das grandes empresas do setor.

    relatorio_profissional.py <saida-do-pipeline> [--triagem triagem.json] [-o AUDITORIA.md]

Sem triagem, gera o rascunho: escopo, metodologia e cobertura vêm do pipeline, e os achados
brutos das ferramentas aparecem no Apêndice A como "pendentes de triagem". A triagem
(modelo/triagem.exemplo.json) é onde o auditor registra os achados confirmados, com
impacto, probabilidade, cenário de exploração, recomendação e resposta do cliente.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from relatorio import CAMADAS, achados_json, ler_json  # noqa: E402

SEVERIDADES = ["Crítica", "Alta", "Média", "Baixa", "Informativa", "Gas"]
PREFIXO = {"Crítica": "C", "Alta": "H", "Média": "M", "Baixa": "L", "Informativa": "I", "Gas": "G"}
STATUS = ["Corrigido", "Parcialmente corrigido", "Reconhecido", "Risco aceito", "Pendente"]

# Impacto x probabilidade, como OpenZeppelin, Spearbit e Cantina classificam.
MATRIZ = {
    ("Alto", "Alta"): "Crítica", ("Alto", "Média"): "Alta", ("Alto", "Baixa"): "Média",
    ("Médio", "Alta"): "Alta", ("Médio", "Média"): "Média", ("Médio", "Baixa"): "Baixa",
    ("Baixo", "Alta"): "Média", ("Baixo", "Média"): "Baixa", ("Baixo", "Baixa"): "Baixa",
}

PENDENTE = "_[a preencher]_"


def severidade(a):
    if a.get("severidade") in SEVERIDADES:
        return a["severidade"]
    return MATRIZ.get((a.get("impacto"), a.get("probabilidade")), "Informativa")


def numerar(achados):
    """IDs no padrão do setor (H-01, M-02...), em ordem de severidade."""
    achados = sorted(achados, key=lambda a: SEVERIDADES.index(severidade(a)))
    cont = Counter()
    for a in achados:
        s = severidade(a)
        cont[s] += 1
        a["_id"] = a.get("id") or f"{PREFIXO[s]}-{cont[s]:02d}"
        a["_sev"] = s
    return achados


def tabela(cab, linhas):
    out = ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    out += ["| " + " | ".join(str(c).replace("|", "/") for c in l) + " |" for l in linhas]
    return out


def secao_achado(a):
    campos = [
        ("Severidade", f"{a['_sev']} (impacto {a.get('impacto', '—')}, probabilidade {a.get('probabilidade', '—')})"),
        ("Status", a.get("status", "Pendente")),
        ("Local", ", ".join(f"`{l}`" for l in ([a["local"]] if isinstance(a.get("local"), str) else a.get("local", []))) or "—"),
        ("Detectado por", a.get("ferramenta", "revisão manual")),
    ]
    out = [f"### {a['_id']} — {a.get('titulo', PENDENTE)}", ""]
    out += [f"- **{k}:** {v}" for k, v in campos]
    for titulo, chave in [
        ("Descrição", "descricao"),
        ("Impacto", "impacto_texto"),
        ("Cenário de exploração", "exploracao"),
        ("Recomendação", "recomendacao"),
        ("Resposta do cliente", "resposta_cliente"),
        ("Revisão da correção", "revisao_correcao"),
    ]:
        valor = a.get(chave)
        if not valor:
            # Informativos não têm impacto nem exploração; resposta e revisão vêm depois do time.
            if chave in ("impacto_texto", "exploracao") and a["_sev"] in ("Informativa", "Gas"):
                continue
            valor = {"resposta_cliente": "Aguardando resposta do time.",
                     "revisao_correcao": "Aguardando correção."}.get(chave, PENDENTE)
        out += ["", f"**{titulo}**", "", valor]
    return out + [""]


def gerar(saida: Path, triagem: dict) -> str:
    inv = ler_json(saida / "inventario" / "resumo.json") or {}
    status = {}
    for p in sorted((saida / "etapas").glob("*/status.json")):
        s = ler_json(p)
        if s:
            status[s["etapa"]] = s
    escopo = []
    tsv = saida / "inventario" / "escopo.tsv"
    if tsv.exists():
        escopo = [l.split("\t") for l in tsv.read_text().splitlines() if l.count("\t") == 2]

    t = triagem
    projeto = t.get("projeto") or Path(str(inv.get("alvo", "projeto"))).name
    achados = numerar([dict(a) for a in t.get("achados", [])])
    por_sev = Counter(a["_sev"] for a in achados)
    por_status = Counter(a.get("status", "Pendente") for a in achados)
    loc = sum(int(e[1]) for e in escopo if e[1].isdigit())
    datas = t.get("datas", {})

    L = [
        f"# Relatório de Auditoria de Segurança — {projeto}",
        "",
        f"**Cliente:** {t.get('cliente', PENDENTE)}  ",
        f"**Versão do relatório:** {t.get('versao', '0.1 (rascunho)')}  ",
        f"**Período:** {datas.get('inicio', PENDENTE)} a {datas.get('fim', PENDENTE)}  ",
        f"**Auditores:** {', '.join(t.get('auditores', [])) or PENDENTE}  ",
        f"**Commit auditado:** `{t.get('commit') or inv.get('commit', 'n/d')}`",
        "",
        "---",
        "",
        "## Sumário",
        "",
        "1. [Aviso legal](#1-aviso-legal)",
        "2. [Resumo executivo](#2-resumo-executivo)",
        "3. [Escopo](#3-escopo)",
        "4. [Metodologia](#4-metodologia)",
        "5. [Classificação de risco](#5-classificação-de-risco)",
        "6. [Achados](#6-achados)",
        "7. [Riscos de centralização e premissas de confiança](#7-riscos-de-centralização-e-premissas-de-confiança)",
        "8. [Apêndices](#8-apêndices)",
        "",
        "## 1. Aviso legal",
        "",
        "Esta auditoria é uma avaliação com tempo limitado do código no commit indicado. Ela não garante "
        "a ausência de vulnerabilidades, não cobre código alterado depois desse commit, nem a infraestrutura, "
        "as chaves ou a operação do protocolo, salvo onde dito. Não é recomendação de investimento. A "
        "responsabilidade pela segurança do protocolo continua sendo de quem o desenvolve e opera.",
        "",
        "## 2. Resumo executivo",
        "",
        "### Visão geral",
        "",
        t.get("resumo") or PENDENTE,
        "",
        "### Contagem de achados",
        "",
    ]
    L += tabela(["Severidade", "Total"] + STATUS,
                [[s, por_sev[s]] + [sum(1 for a in achados if a["_sev"] == s and a.get("status", "Pendente") == st) for st in STATUS]
                 for s in SEVERIDADES])
    L += ["", "### Avaliação geral", ""]
    av = t.get("avaliacao", {})
    L += tabela(["Critério", "Avaliação", "Comentário"], [
        [c, av.get(k, {}).get("nota", PENDENTE), av.get(k, {}).get("comentario", "")]
        for c, k in [("Qualidade do código", "codigo"), ("Documentação", "documentacao"),
                     ("Testes e cobertura", "testes"), ("Controle de acesso", "acesso"),
                     ("Descentralização", "descentralizacao"), ("Aritmética e arredondamento", "aritmetica")]
    ])

    L += ["", "## 3. Escopo", "",
          f"- **Repositório:** {inv.get('alvo', PENDENTE)}",
          f"- **Commit:** `{t.get('commit') or inv.get('commit', 'n/d')}`",
          f"- **Tipo de projeto:** {inv.get('projeto', 'n/d')}",
          f"- **Arquivos no escopo:** {len(escopo)} ({loc:,} linhas)".replace(",", "."),
          "", "Lista completa com hash no [Apêndice C](#apêndice-c--arquivos-no-escopo).", "",
          "**Fora do escopo:**", ""]
    L += [f"- {x}" for x in t.get("fora_do_escopo", [])] or [PENDENTE]
    # Seções livres da triagem (ex.: verificação dos contratos implantados), dentro do escopo.
    for s in t.get("secoes_extras", []):
        L += ["", f"### {s['titulo']}", "", s.get("markdown", "")]

    L += ["", "## 4. Metodologia", "",
          "A auditoria combinou análise automatizada e revisão manual linha a linha:", "",
          "1. **Inventário e modelagem de ameaças:** mapeamento de componentes, fluxos de valor, papéis e premissas de confiança.",
          "2. **Análise automatizada:** o pipeline `auditoria-web3` executou as ferramentas abaixo; todo achado automático foi triado manualmente antes de entrar neste relatório.",
          "3. **Revisão manual:** leitura do código no escopo, com foco em lógica de negócio, controle de acesso, contabilidade, oráculos, integrações externas e casos de borda.",
          "4. **Testes dirigidos:** fuzzing de invariantes e provas de conceito para os achados confirmados.",
          ""]
    linhas = []
    for camada, etapas in CAMADAS:
        for e in etapas:
            s = status.get(e)
            if s:
                linhas.append([camada, s["ferramenta"], s["status"], "—" if s["achados"] is None else s["achados"], s["mensagem"]])
    L += tabela(["Camada", "Ferramenta", "Status", "Sinais brutos", "Observação"], linhas) if linhas else [PENDENTE]

    L += ["", "## 5. Classificação de risco", "",
          "A severidade combina o **impacto** (o dano se explorado) e a **probabilidade** (o quão fácil e provável é a exploração).", ""]
    L += tabela(["Impacto \\ Probabilidade", "Alta", "Média", "Baixa"],
                [[i] + [MATRIZ[(i, p)] for p in ("Alta", "Média", "Baixa")] for i in ("Alto", "Médio", "Baixo")])
    L += ["",
          "- **Crítica:** perda ou congelamento direto de fundos, ou controle do protocolo, sem pré-condições relevantes.",
          "- **Alta:** perda de fundos ou quebra de propriedade central, com pré-condições plausíveis.",
          "- **Média:** perda limitada, negação de serviço relevante ou quebra de premissa que depende de condições específicas.",
          "- **Baixa:** impacto pequeno ou exploração muito improvável.",
          "- **Informativa:** boas práticas, documentação, legibilidade; sem risco direto.",
          "- **Gas:** otimizações de custo.",
          "", "**Status:** Corrigido · Parcialmente corrigido · Reconhecido (o cliente não corrige agora) · "
          "Risco aceito (decisão de projeto) · Pendente.", "",
          "## 6. Achados", "", "### Resumo", ""]
    L += tabela(["ID", "Título", "Severidade", "Status"],
                [[a["_id"], a.get("titulo", PENDENTE), a["_sev"], a.get("status", "Pendente")] for a in achados]) if achados \
        else ["Nenhum achado triado ainda. Os sinais brutos das ferramentas estão no [Apêndice A](#apêndice-a--saída-bruta-das-ferramentas)."]
    L += [""]
    for sev in SEVERIDADES:
        grupo = [a for a in achados if a["_sev"] == sev]
        if grupo:
            L += [f"### Severidade {sev}", ""]
            for a in grupo:
                L += secao_achado(a)

    L += ["## 7. Riscos de centralização e premissas de confiança", ""]
    L += [f"- {x}" for x in t.get("riscos_centralizacao", [])] or [PENDENTE]

    L += ["", "## 8. Apêndices", "", "### Apêndice A — Saída bruta das ferramentas", "",
          "Sinais automáticos ainda **não triados**. Não são achados confirmados.", ""]
    brutos = []
    for e, s in status.items():
        for sev, titulo, local, ferr in achados_json(saida / "etapas" / e)[:100]:
            brutos.append([s["ferramenta"], sev, titulo, f"`{local}`", ferr])
    L += tabela(["Etapa", "Severidade (ferramenta)", "Sinal", "Local", "Detector"], brutos) if brutos else ["Nenhum sinal estruturado."]

    L += ["", "### Apêndice B — Cobertura por etapa", "",
          "Etapas puladas ou com erro não foram cobertas por ferramenta automática e devem ser compensadas na revisão manual.", ""]
    L += tabela(["Etapa", "Status", "Motivo"], [[s["ferramenta"], s["status"], s["mensagem"]] for s in status.values() if s["status"] != "ok"]) \
        if any(s["status"] != "ok" for s in status.values()) else ["Todas as etapas concluíram."]

    L += ["", "### Apêndice C — Arquivos no escopo", ""]
    L += tabela(["Arquivo", "Linhas", "SHA-256 (16)"], [[f"`{e[2]}`", e[1], f"`{e[0]}`"] for e in escopo]) if escopo else [PENDENTE]

    L += ["", "### Apêndice D — Histórico de revisões", ""]
    L += tabela(["Versão", "Data", "Descrição"],
                t.get("historico") or [[t.get("versao", "0.1"), date.today().isoformat(), "Rascunho gerado pelo pipeline"]])
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("saida", type=Path)
    ap.add_argument("--triagem", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    a = ap.parse_args()
    triagem = json.loads(a.triagem.read_text()) if a.triagem else {}
    destino = a.output or a.saida / "AUDITORIA.md"
    destino.write_text(gerar(a.saida, triagem))
    print(f"Relatório de auditoria: {destino}")


if __name__ == "__main__":
    main()
