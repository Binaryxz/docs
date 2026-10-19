#!/usr/bin/env python3
"""Consolida os resultados das etapas em RELATORIO.md e resumo.json."""

import json
import sys
from pathlib import Path

ICONES = {"ok": "✅", "pulada": "⏭️", "erro": "❌"}

CAMADAS = [
    ("Contratos (EVM)", ["10-miesc", "11-solidityguard", "12-symgpt", "13-medusa"]),
    ("Circuitos ZK (Circom)", ["20-picus", "21-circheck"]),
    ("Bridges, privacidade e zkTLS", ["30-bridgeguard", "31-privacidade", "32-zktls"]),
    ("Governança (DAO)", ["40-governanca", "41-quorum"]),
    ("Infraestrutura off-chain", ["50-nodeprobe"]),
]


def ler_json(caminho):
    try:
        return json.loads(Path(caminho).read_text())
    except (OSError, ValueError):
        return None


ORDEM_SEV = {"critical": 0, "critica": 0, "high": 1, "alta": 1, "medium": 2, "media": 2,
             "low": 3, "baixa": 3, "info": 4, "informational": 4, "optimization": 5}
IGNORAR = {"status.json", "mcp.json"}


def _local(o):
    loc = o.get("location")
    if isinstance(loc, dict):
        arq, linha = loc.get("file") or o.get("file") or "", loc.get("line")
        return f"{arq}:{linha}" if linha else arq
    return str(loc or o.get("file") or "")


def achados_json(etapa_dir):
    """Achados estruturados (objetos com "severity") nos JSONs da etapa, sem duplicatas."""
    vistos, saida = set(), []
    for arq in sorted(etapa_dir.glob("*.json")):
        if arq.name in IGNORAR:
            continue
        pilha = [ler_json(arq)]
        while pilha:
            o = pilha.pop()
            if isinstance(o, list):
                pilha.extend(o)
            elif isinstance(o, dict):
                if isinstance(o.get("severity"), str):
                    titulo = str(o.get("title") or o.get("type") or o.get("check") or o.get("message") or "")[:120]
                    item = (o["severity"], titulo, _local(o), str(o.get("tool") or ""))
                    if item not in vistos:
                        vistos.add(item)
                        saida.append(item)
                else:
                    pilha.extend(o.values())
    return sorted(saida, key=lambda a: ORDEM_SEV.get(a[0].lower(), 9))


def main(saida):
    saida = Path(saida)
    etapas_dir = saida / "etapas"
    inv = ler_json(saida / "inventario" / "resumo.json") or {}
    status = {}
    for p in sorted(etapas_dir.glob("*/status.json")):
        s = ler_json(p)
        if s:
            status[s["etapa"]] = s

    total = sum(s["achados"] or 0 for s in status.values() if s["status"] == "ok")
    linhas = [
        "# Relatório de auditoria Web3",
        "",
        f"- **Alvo:** {inv.get('alvo', 'n/d')}",
        f"- **Commit:** `{inv.get('commit', 'n/d')}`",
        f"- **Data:** {inv.get('data', 'n/d')}",
        f"- **Tipo de projeto:** {inv.get('projeto', 'n/d')}",
        f"- **Achados/sinais brutos (todas as ferramentas):** {total}",
        "",
        "> Os números são saídas brutas das ferramentas e incluem falsos positivos.",
        "> Cada achado precisa ser triado por um auditor antes de ser considerado real.",
        "",
        "## Inventário",
        "",
        "| Categoria | Arquivos |",
        "|---|---|",
    ]
    for k, v in (inv.get("arquivos") or {}).items():
        linhas.append(f"| {k} | {v} |")

    conhecidas = {e for _, es in CAMADAS for e in es}
    extras = [e for e in status if e not in conhecidas]
    for titulo, etapas in CAMADAS + ([("Outras", extras)] if extras else []):
        linhas += ["", f"## {titulo}", "", "| Etapa | Ferramenta | Status | Achados | Tempo | Detalhe |", "|---|---|---|---|---|---|"]
        for e in etapas:
            s = status.get(e)
            if not s:
                linhas.append(f"| {e} | — | não executada | — | — | — |")
                continue
            achados = "—" if s["achados"] is None else s["achados"]
            linhas.append(
                f"| [{e}](etapas/{e}/) | {s['ferramenta']} | {ICONES.get(s['status'], '')} {s['status']} "
                f"| {achados} | {s['duracao_s']}s | {s['mensagem'].replace('|', '/')} |"
            )

    # Achados estruturados das ferramentas que emitem JSON.
    for e, s in status.items():
        itens = achados_json(etapas_dir / e)
        if not itens:
            continue
        limite = 200
        linhas += ["", f"## Achados: {s['ferramenta']}", "", "| Severidade | Achado | Local | Detector |", "|---|---|---|---|"]
        for sev, titulo, local, ferramenta in itens[:limite]:
            linhas.append(f"| {sev} | {titulo.replace('|', '/')} | `{local}` | {ferramenta} |")
        if len(itens) > limite:
            linhas.append(f"| … | +{len(itens) - limite} achados nos JSONs da etapa | | |")

    # Relatórios das revisões por LLM entram na íntegra.
    for e, s in status.items():
        rel = etapas_dir / e / "relatorio.md"
        if s["status"] == "ok" and rel.exists() and rel.stat().st_size:
            linhas += ["", "---", "", f"## Revisão: {s['ferramenta']}", "", rel.read_text().strip()]

    pendentes = [s for s in status.values() if s["status"] != "ok"]
    if pendentes:
        linhas += ["", "## Etapas não concluídas", ""]
        linhas += [f"- **{s['ferramenta']}** ({s['status']}): {s['mensagem']}" for s in pendentes]

    (saida / "RELATORIO.md").write_text("\n".join(linhas) + "\n")
    (saida / "resumo.json").write_text(
        json.dumps({"inventario": inv, "etapas": list(status.values()), "achados_total": total}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: relatorio.py <diretorio-de-saida>")
    main(sys.argv[1])
