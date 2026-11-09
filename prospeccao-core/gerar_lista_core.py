"""
Gera a lista de prospecção da Core (escritórios de advocacia e de contabilidade) a partir dos
arquivos abertos de CNPJ da Receita Federal, no mesmo espírito de
prospeccao-fintech/scripts/filtrar_cnpjs_local.py, mas com o ICP da Core:

  - CNAE principal 6911-7/01 (serviços advocatícios), 6920-6/01 (atividades de contabilidade)
    ou 6920-6/02 (consultoria e auditoria contábil e tributária);
  - situação cadastral ativa, só a matriz;
  - telefone celular válido (é o que tem WhatsApp na prática); celular antigo de 8 dígitos
    ganha o 9 na frente, e placeholders (99999999 etc.) são descartados;
  - um lead por telefone (escritórios que dividem número contam uma vez).

Os arquivos são baixados um por vez do espelho da Casa dos Dados (a Receita bloqueia nuvem),
lidos por streaming de dentro do zip e apagados em seguida, então o disco usado é de ~1 arquivo.

Uso:
    python gerar_lista_core.py --saida leads_core.csv [--snapshot 2026-10-13] [--dir /tmp/cnpj]
    python gerar_lista_core.py --saida leads_core.csv --locais "dados/Estabelecimentos*.zip" \
        --empresas-locais "dados/Empresas*.zip"

Saída (CSV UTF-8): telefone (E.164 sem +), cnpj, razao_social, nome_fantasia, segmento, uf,
municipio, data_inicio, email, capital_social, porte.

Dados de empresa são públicos, mas telefone e e-mail de sociedade individual são de uma pessoa:
trate o CSV como dado pessoal (LGPD), não versione e apague depois de importar.
"""

import argparse
import csv
import glob
import io
import os
import re
import subprocess
import sys
import zipfile

ESPELHO = "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos"
CNAES = {"6911701": "advocacia", "6920601": "contabilidade", "6920602": "contabilidade"}
PORTE = {"00": "nao_informado", "01": "micro", "03": "pequena", "05": "demais"}


def ultimo_snapshot() -> str:
    html = subprocess.run(["curl", "-sS", "-m", "60", ESPELHO + "/"], capture_output=True, text=True).stdout
    return sorted(re.findall(r'href="(20\d\d-\d\d-\d\d)/"', html))[-1]


def baixar(snapshot: str, nome: str, pasta: str) -> str:
    destino = os.path.join(pasta, nome)
    if not os.path.exists(destino):
        print(f"  baixando {nome}...", file=sys.stderr)
        subprocess.run(["curl", "-sS", "-f", "--retry", "4", "-o", destino + ".part",
                        f"{ESPELHO}/{snapshot}/{nome}"], check=True)
        os.rename(destino + ".part", destino)
    return destino


def linhas_zip(caminho: str):
    with zipfile.ZipFile(caminho) as z:
        for membro in z.namelist():
            with z.open(membro) as f:
                yield from io.TextIOWrapper(f, encoding="latin-1", newline="")


def celular(ddd: str, numero: str) -> str:
    ddd, numero = re.sub(r"\D", "", ddd or ""), re.sub(r"\D", "", numero or "")
    if len(ddd) == 3 and ddd.startswith("0"):
        ddd = ddd[1:]
    if len(ddd) != 2 or ddd[0] == "0":
        return ""
    if len(numero) == 8 and numero[0] in "6789":
        numero = "9" + numero
    if len(numero) != 9 or numero[0] != "9" or len(set(numero[1:])) <= 2:
        return ""
    return f"55{ddd}{numero}"


def escanear_estabelecimentos(arquivos):
    alvo = tuple(f'"{c}"' for c in CNAES)
    leads = {}
    for arq in arquivos:
        n = 0
        for linha in arquivos[arq]() if callable(arquivos[arq]) else linhas_zip(arq):
            if not any(c in linha for c in alvo):
                continue
            r = next(csv.reader([linha], delimiter=";"))
            if len(r) < 28 or r[3] != "1" or r[5].lstrip("0") != "2" or r[11] not in CNAES:
                continue
            tel = celular(r[21], r[22]) or celular(r[23], r[24])
            if not tel or tel in leads:
                continue
            leads[tel] = {"telefone": tel, "cnpj_basico": r[0], "cnpj": r[0] + r[1] + r[2],
                          "nome_fantasia": r[4].strip(), "segmento": CNAES[r[11]], "uf": r[19],
                          "municipio": r[20], "data_inicio": r[10], "email": r[27].strip().lower()}
            n += 1
        print(f"  {os.path.basename(arq)}: +{n} (total {len(leads)})", file=sys.stderr)
    return leads


def completar_empresas(leads: dict, arquivos: list[str]) -> None:
    por_basico = {}
    for v in leads.values():
        por_basico.setdefault(v["cnpj_basico"], []).append(v)
    for arq in arquivos:
        for linha in linhas_zip(arq):
            basico = linha[1:9]
            if basico not in por_basico:
                continue
            r = next(csv.reader([linha], delimiter=";"))
            for v in por_basico[basico]:
                v["razao_social"] = r[1].strip()
                v["capital_social"] = r[4].replace(",", ".")
                v["porte"] = PORTE.get(r[5], r[5])
        print(f"  {os.path.basename(arq)} lido", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saida", required=True)
    ap.add_argument("--snapshot", default=None, help="pasta do espelho (AAAA-MM-DD); padrão: a mais recente")
    ap.add_argument("--dir", default="/tmp/cnpj", help="pasta temporária dos zips")
    ap.add_argument("--locais", nargs="*", help="zips de Estabelecimentos já baixados (pula o download)")
    ap.add_argument("--empresas-locais", nargs="*", help="zips de Empresas já baixados")
    ap.add_argument("--manter", action="store_true", help="não apagar os zips depois de ler")
    a = ap.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    snap = None if a.locais else (a.snapshot or ultimo_snapshot())
    print(f"snapshot: {snap or 'arquivos locais'}", file=sys.stderr)

    def fonte(prefixo: str, locais):
        if locais:
            return [p for padrao in locais for p in sorted(glob.glob(padrao))]
        return [f"{prefixo}{i}.zip" for i in range(10)]

    leads = {}
    print("[1/2] estabelecimentos", file=sys.stderr)
    for nome in fonte("Estabelecimentos", a.locais):
        caminho = nome if a.locais else baixar(snap, nome, a.dir)
        novos = escanear_estabelecimentos({caminho: None})
        for k, v in novos.items():
            leads.setdefault(k, v)
        if not a.locais and not a.manter:
            os.remove(caminho)
    print(f"[2/2] empresas (razão social) para {len(leads)} leads", file=sys.stderr)
    for nome in fonte("Empresas", a.empresas_locais if a.locais else None):
        caminho = nome if a.locais else baixar(snap, nome, a.dir)
        completar_empresas(leads, [caminho])
        if not a.locais and not a.manter:
            os.remove(caminho)

    campos = ["telefone", "cnpj", "razao_social", "nome_fantasia", "segmento", "uf", "municipio",
              "data_inicio", "email", "capital_social", "porte"]
    with open(a.saida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for v in sorted(leads.values(), key=lambda v: v["data_inicio"], reverse=True):
            w.writerow(v)
    print(f"{len(leads)} leads -> {a.saida}", file=sys.stderr)


if __name__ == "__main__":
    main()
