"""
Filtra a base aberta de CNPJ da Receita Federal para UM segmento por vez —
advocacia empresarial ou contabilidade empresarial — identifica o decisor
provável (sócio-administrador) e gera um CSV de leads.

Adaptação de prospeccao-fintech/scripts/filtrar_cnpjs_local.py (mesma
leitura por streaming direto dos zips da Receita, sem BigQuery), com três
diferenças:

  1. CNAES_ALVO / palavras-chave vêm do segmento escolhido (SEGMENTOS abaixo).
     Rode uma vez por segmento, com saídas separadas — nunca misture as
     keywords dos dois (as negativas de um só fazem sentido pro outro).
  2. Keywords NEGATIVAS (PALAVRAS_CHAVE_EXCLUSAO do segmento): se aparecerem
     na razão social OU no nome fantasia, o CNPJ sai, não importa se bateu
     CNAE ou keyword positiva.
  3. Exclusão hard de CNAE principal (CNAES_EXCLUIR — ex.: 6912501, cartório).

Regra de entrada (padrão):
  CNPJ ativo (situação 02) cujo estabelecimento atinja o limiar de CNAEs alvo
  do segmento (principal + secundários, com peso). Com os pesos/limiar
  padrão isso equivale a "tem o CNAE principal do segmento":
    advocacia: 6911701 (6911702 sozinho não basta)
    contábil:  6920601 ou 6920602
  As keywords POSITIVAS não decidem entrada: só ordenam por relevância
  (coluna keywords_positivas / relevancia). Motivo: termos como
  EMPRESARIAL, CONTRATOS, AUDITORIA, CONSULTIVA aparecem em dezenas de
  milhares de empresas que não são escritórios; sem o CNAE como portão o
  filtro vira ruído. Para aceitar também quem só bateu keyword positiva
  (sem CNAE do segmento), use --incluir-so-keyword. Para manter só quem tem
  CNAE E keyword positiva (lista menor, mais "empresarial"), use
  --exigir-keyword.

Comparação de keywords: sem acento, minúsculas, pontuação vira espaço, e o
termo tem que bater como PALAVRA inteira (ex.: "MEI" não casa "Meireles",
"PENAL" não casa "Penalva"). Termo terminado em "*" é prefixo
("CORPORAT*" casa corporativo, corporativa, corporate...).

Uso:
    python filtrar_cnpjs_segmento.py --segmento advocacia \
      --estabelecimentos "dados_rf/2026-09-14/Estabelecimentos*.zip" \
      --empresas "dados_rf/2026-09-14/Empresas*.zip" \
      --socios "dados_rf/2026-09-14/Socios*.zip" \
      --municipios dados_rf/2026-09-14/Municipios.zip \
      --saida leads_advocacia.csv

Sem --capital-maximo não há teto de capital social: no final o script
imprime a distribuição de capital_social dos leads, pra decidir o corte
olhando o dado real (e rodar de novo com --capital-maximo, se for o caso).
"""

import argparse
import csv
import glob
import io
import re
import statistics
import sys
import unicodedata
import zipfile
from typing import Iterable, Iterator

SEGMENTOS = {
    "advocacia": {
        # peso 2 = principal (obrigatório na prática), peso 1 = opcional
        "cnaes_alvo": {
            "6911701": 2,  # Serviços advocatícios
            "6911702": 1,  # Atividades auxiliares da justiça
        },
        "cnaes_excluir": {"6912501"},  # Serviços notariais e de registro (cartório)
        # Só sinalizam perfil empresarial (ordenação), não decidem entrada
        "cnaes_sinal": {"6920602", "7020400", "6201501", "6209100", "6311900"},
        "positivas": [
            "SOCIEDADE DE ADVOGADOS", "ADVOGADOS ASSOCIADOS", "ADVOCACIA EMPRESARIAL",
            "ADVOGADOS EMPRESARIAIS", "ASSESSORIA JURIDICA", "ASSESSORIA JURIDICA EMPRESARIAL",
            "CONSULTORIA JURIDICA", "JURIDICO EMPRESARIAL", "BOUTIQUE JURIDICA", "SOCIETARIO",
            "SOCIETARIA", "TRIBUTARIO", "TRIBUTARIA", "CORPORAT*", "CONTRATOS", "COMPLIANCE",
            "EMPRESARIAL",
        ],
        "negativas": [
            "FAMILIAR", "FAMILIA", "DIVORCIO", "INVENTARIO", "SUCESSOES", "PENAL", "CRIMINAL",
            "CRIMINAIS", "PREVIDENCIARIO", "INSS", "APOSENTADORIA", "PENSAO", "INDENIZACAO",
            "ACIDENTE", "CONSUMIDOR", "IMOBILIARIO", "DANOS MORAIS", "ERRO MEDICO", "BANCARIO",
            "AUXILIO-DOENCA",
        ],
    },
    "contabil": {
        "cnaes_alvo": {
            "6920601": 2,  # Atividades de contabilidade
            "6920602": 2,  # Atividades de consultoria e auditoria contábil e tributária
        },
        "cnaes_excluir": set(),
        "cnaes_sinal": {"7020400", "6201501", "6209100", "6311900"},
        "positivas": [
            "ASSESSORIA CONTABIL", "CONTABILIDADE EMPRESARIAL", "CONTABIL EMPRESARIAL",
            "CONTABILIDADE E AUDITORIA", "AUDITORIA", "AUDITOR*", "AUDIT", "BPO FINANCEIRO", "BPO FISCAL",
            "BPO CONTABIL", "CONSULTIVA", "CONSULTORIA CONTABIL", "CONSULTORIA TRIBUTARIA",
            "GESTAO TRIBUTARIA", "PLANEJAMENTO TRIBUTARIO", "CONTABILIDADE GERENCIAL",
            "CONTROLADORIA", "CONTROLLER",
            # "AUDIT*" (prefixo puro) casava "aparelhos auditivos"/"próteses
            # auditivas" na base real — AUDITOR* cobre auditor/auditores/
            # auditoria e AUDIT (palavra) cobre o termo em inglês.
        ],
        "negativas": [
            "MEI", "MEIS", "AUTONOMOS", "PARA MEDICOS", "PARA DENTISTAS", "PROFISSIONAIS LIBERAIS",
            "SIMPLES", "CURSOS", "ESCOLA", "EDUCACIONAL", "FRANQUIA", "PROMOCIONAL", "PARA VOCE",
        ],
    },
}

LIMIAR_PADRAO = 0.35
QUALIFICACOES_DECISOR = {"10", "16", "22", "49", "65"}

CAMPOS_ESTABELECIMENTOS = [
    "cnpj_basico", "cnpj_ordem", "cnpj_dv", "identificador_matriz_filial",
    "nome_fantasia", "situacao_cadastral", "data_situacao_cadastral",
    "motivo_situacao_cadastral", "nome_cidade_exterior", "pais",
    "data_inicio_atividade", "cnae_fiscal_principal", "cnae_fiscal_secundaria",
    "tipo_logradouro", "logradouro", "numero", "complemento", "bairro", "cep",
    "uf", "municipio", "ddd_1", "telefone_1", "ddd_2", "telefone_2",
    "ddd_fax", "fax", "correio_eletronico", "situacao_especial",
    "data_situacao_especial",
]
CAMPOS_EMPRESAS = [
    "cnpj_basico", "razao_social", "natureza_juridica",
    "qualificacao_responsavel", "capital_social", "porte_empresa",
    "ente_federativo_responsavel",
]
CAMPOS_SOCIOS = [
    "cnpj_basico", "identificador_socio", "nome_socio_razao_social",
    "cnpj_cpf_socio", "qualificacao_socio", "data_entrada_sociedade", "pais",
    "representante_legal", "nome_representante",
    "qualificacao_representante_legal", "faixa_etaria",
]
CAMPOS_MUNICIPIOS = ["codigo", "descricao"]

COLUNAS_SAIDA = [
    "segmento", "cnpj", "razao_social", "nome_fantasia", "relevancia",
    "keywords_positivas", "cnaes_sinal_encontrados", "percentual_match_cnae",
    "qtd_cnaes_alvo_encontrados", "motivo_match", "cnae_fiscal_principal",
    "cnae_fiscal_secundaria", "data_inicio_atividade", "capital_social",
    "porte_empresa", "natureza_juridica", "uf", "municipio", "nome_municipio",
    "ddd_1", "telefone_1", "ddd_2", "telefone_2", "correio_eletronico",
    "nome_decisor", "documento_decisor", "qualificacao_socio", "eh_decisor",
    "data_entrada_sociedade",
]


def expandir(padroes: list[str]) -> list[str]:
    caminhos = []
    for padrao in padroes:
        encontrados = sorted(glob.glob(padrao))
        if not encontrados:
            print(f"[aviso] nenhum arquivo encontrado para o padrão: {padrao}", file=sys.stderr)
        caminhos.extend(encontrados)
    return caminhos


def normaliza(texto: str) -> str:
    """Sem acento, minúsculas, tudo que não é letra/dígito vira espaço."""
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texto.lower()).split())


def compila_termos(termos: list[str]) -> list[tuple[str, re.Pattern]]:
    """Cada termo vira uma regex de palavra inteira sobre o texto normalizado;
    'XYZ*' vira prefixo."""
    compilados = []
    for termo in termos:
        prefixo = termo.endswith("*")
        base = normaliza(termo.rstrip("*"))
        sufixo = r"[a-z0-9]*" if prefixo else r"(?![a-z0-9])"
        compilados.append((termo, re.compile(r"(?<![a-z0-9])" + re.escape(base) + sufixo)))
    return compilados


def termos_encontrados(texto: str, compilados: list[tuple[str, re.Pattern]]) -> list[str]:
    t = normaliza(texto)
    return [termo for termo, rx in compilados if rx.search(t)]


def cnaes_do_estabelecimento(principal: str, secundaria: str) -> set[str]:
    todos = set()
    if principal:
        todos.add(principal.strip())
    if secundaria:
        todos.update(s.strip() for s in secundaria.split(",") if s.strip())
    return todos


def percentual_cnae(cnaes: set[str], cnaes_alvo: dict[str, int]) -> tuple[float, int]:
    encontrados = cnaes & cnaes_alvo.keys()
    return sum(cnaes_alvo[c] for c in encontrados) / sum(cnaes_alvo.values()), len(encontrados)


def _linhas_de_bufferedreader(f_bin, campos_padrao: list[str]) -> Iterator[dict]:
    """Detecta encoding/delimitador/cabeçalho por uma amostra grande do início
    do arquivo e devolve dicts com nomes de coluna padronizados (CAMPOS_*)."""
    amostra = f_bin.read(1_000_000)
    encoding = "utf-8-sig"
    try:
        amostra.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "latin-1"
    f_bin.seek(0)

    primeira_linha = amostra.split(b"\n", 1)[0].decode(encoding, errors="replace")
    delimitador = ";" if primeira_linha.count(";") >= primeira_linha.count(",") else ","
    tem_cabecalho = any(
        campo in primeira_linha for campo in ("cnpj_basico", "razao_social", "nome_socio_razao_social")
    )

    texto = io.TextIOWrapper(f_bin, encoding=encoding, newline="", errors="replace")
    if tem_cabecalho:
        reader = csv.DictReader(texto, delimiter=delimitador)
    else:
        reader = csv.DictReader(texto, fieldnames=campos_padrao, delimiter=delimitador)
    yield from reader


def abrir_linhas(caminho: str, campos_padrao: list[str]) -> Iterator[dict]:
    if caminho.lower().endswith(".zip"):
        with zipfile.ZipFile(caminho) as z:
            for nome in (n for n in z.namelist() if not n.endswith("/")):
                with z.open(nome) as f_bin:
                    yield from _linhas_de_bufferedreader(f_bin, campos_padrao)
    else:
        with open(caminho, "rb") as f_bin:
            yield from _linhas_de_bufferedreader(f_bin, campos_padrao)


def escanear_estabelecimentos(
    caminhos: list[str],
    seg: dict,
    positivas: list[tuple[str, re.Pattern]],
    limiar: float,
    incluir_so_keyword: bool,
    ids_extra: Iterable[str] = (),
) -> tuple[set[str], dict[str, dict]]:
    accepted: set[str] = set(ids_extra)
    estab_data: dict[str, dict] = {}
    for caminho in caminhos:
        for row in abrir_linhas(caminho, CAMPOS_ESTABELECIMENTOS):
            if row.get("situacao_cadastral") != "02":  # 02 = ATIVA
                continue
            cnpj_basico = (row.get("cnpj_basico") or "").strip()
            if not cnpj_basico:
                continue

            cnaes = cnaes_do_estabelecimento(row.get("cnae_fiscal_principal", ""), row.get("cnae_fiscal_secundaria", ""))
            pct, qtd = percentual_cnae(cnaes, seg["cnaes_alvo"])
            match_cnae = pct >= limiar
            match_nome = incluir_so_keyword and bool(termos_encontrados(row.get("nome_fantasia", ""), positivas))
            ja_aceito = cnpj_basico in accepted

            if not (match_cnae or match_nome or ja_aceito):
                continue

            accepted.add(cnpj_basico)
            motivo = "cnae" if match_cnae else ("nome_fantasia" if match_nome else "razao_social")
            atual = estab_data.get(cnpj_basico)
            eh_matriz = row.get("identificador_matriz_filial") == "1"
            # Prefere o estabelecimento que bateu CNAE e, entre esses, a matriz
            rank = (0 if match_cnae else 1, 0 if eh_matriz else 1)
            if atual is None or rank < atual["_rank"]:
                estab_data[cnpj_basico] = {
                    **row,
                    "percentual_match_cnae": round(pct, 2),
                    "qtd_cnaes_alvo_encontrados": qtd,
                    "motivo_match": motivo,
                    "cnaes_sinal_encontrados": "|".join(sorted(cnaes & seg["cnaes_sinal"])),
                    "_rank": rank,
                }
    return accepted, estab_data


def escanear_empresas(
    caminhos: list[str],
    accepted_estab_ids: set[str],
    positivas: list[tuple[str, re.Pattern]],
    incluir_so_keyword: bool,
) -> tuple[dict[str, dict], set[str]]:
    empresas: dict[str, dict] = {}
    novos_via_razao_social: set[str] = set()
    for caminho in caminhos:
        for row in abrir_linhas(caminho, CAMPOS_EMPRESAS):
            cnpj_basico = (row.get("cnpj_basico") or "").strip()
            if not cnpj_basico:
                continue
            razao = row.get("razao_social", "")
            ja_aceito = cnpj_basico in accepted_estab_ids
            match_razao = incluir_so_keyword and not ja_aceito and bool(termos_encontrados(razao, positivas))
            if not (ja_aceito or match_razao):
                continue
            if match_razao:
                novos_via_razao_social.add(cnpj_basico)
            bruto = (row.get("capital_social") or "").strip().replace(",", ".")
            try:
                capital = float(bruto)
            except ValueError:
                capital = 0.0
            empresas[cnpj_basico] = {
                "razao_social": razao,
                "capital_social": capital,
                "porte_empresa": row.get("porte_empresa", ""),
                "natureza_juridica": row.get("natureza_juridica", ""),
            }
    return empresas, novos_via_razao_social


def escanear_socios(caminhos: list[str], accepted_ids: set[str]) -> dict[str, dict]:
    melhor: dict[str, tuple] = {}
    for caminho in caminhos:
        for row in abrir_linhas(caminho, CAMPOS_SOCIOS):
            cnpj_basico = (row.get("cnpj_basico") or "").strip()
            if cnpj_basico not in accepted_ids:
                continue
            qualificacao = row.get("qualificacao_socio", "")
            eh_decisor = qualificacao in QUALIFICACOES_DECISOR
            rank = (0 if eh_decisor else 1, row.get("data_entrada_sociedade") or "99999999")
            atual = melhor.get(cnpj_basico)
            if atual is None or rank < atual[0]:
                melhor[cnpj_basico] = (
                    rank,
                    {
                        "nome_decisor": row.get("nome_socio_razao_social", ""),
                        "documento_decisor": row.get("cnpj_cpf_socio", ""),
                        "qualificacao_socio": qualificacao,
                        "eh_decisor": "1" if eh_decisor else "0",
                        "data_entrada_sociedade": row.get("data_entrada_sociedade", ""),
                    },
                )
    return {k: v[1] for k, v in melhor.items()}


def carregar_municipios(caminho: str | None) -> dict[str, str]:
    if not caminho:
        return {}
    return {
        (r.get("codigo") or "").strip(): (r.get("descricao") or "").strip()
        for r in abrir_linhas(caminho, CAMPOS_MUNICIPIOS)
    }


def imprimir_distribuicao_capital(valores: list[float]) -> None:
    if not valores:
        return
    valores = sorted(valores)
    n = len(valores)
    quantis = statistics.quantiles(valores, n=100, method="inclusive") if n > 1 else [valores[0]] * 99
    print("      distribuição de capital_social (R$):", file=sys.stderr)
    for rotulo, q in (("p10", 10), ("p25", 25), ("mediana", 50), ("p75", 75), ("p90", 90), ("p95", 95), ("p99", 99)):
        print(f"        {rotulo:>8}: {quantis[q - 1]:>16,.2f}", file=sys.stderr)
    print(f"        {'máximo':>8}: {valores[-1]:>16,.2f}", file=sys.stderr)
    faixas = [0, 1_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000, float("inf")]
    for lo, hi in zip(faixas, faixas[1:]):
        qtd = sum(1 for v in valores if lo <= v < hi)
        rotulo_hi = "+" if hi == float("inf") else f"{hi:,.0f}"
        print(f"        [{lo:>11,.0f} .. {rotulo_hi:>11}): {qtd:>7} ({qtd / n:.1%})", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--segmento", required=True, choices=sorted(SEGMENTOS), help="Segmento a filtrar (um por execução)")
    parser.add_argument("--estabelecimentos", nargs="+", required=True, help="Caminho(s)/glob dos arquivos de Estabelecimentos")
    parser.add_argument("--empresas", nargs="+", required=True, help="Caminho(s)/glob dos arquivos de Empresas")
    parser.add_argument("--socios", nargs="+", default=None, help="Caminho(s)/glob dos arquivos de Sócios (opcional)")
    parser.add_argument("--municipios", default=None, help="Municipios.zip da Receita (opcional; preenche nome_municipio)")
    parser.add_argument("--saida", required=True, help="CSV de saída (ex.: leads_advocacia.csv)")
    parser.add_argument("--limiar", type=float, default=LIMIAR_PADRAO, help=f"Percentual mínimo (ponderado) de CNAEs alvo (padrão {LIMIAR_PADRAO})")
    parser.add_argument("--capital-maximo", type=float, default=None, help="Capital social máximo (R$); omitido = sem teto")
    parser.add_argument("--incluir-so-keyword", action="store_true", help="Aceita também quem só bateu keyword positiva, sem CNAE do segmento")
    parser.add_argument("--exigir-keyword", action="store_true", help="Mantém só quem bateu keyword positiva (além do CNAE)")
    args = parser.parse_args()

    seg = SEGMENTOS[args.segmento]
    positivas = compila_termos(seg["positivas"])
    negativas = compila_termos(seg["negativas"])

    caminhos_estab = expandir(args.estabelecimentos)
    caminhos_emp = expandir(args.empresas)
    caminhos_soc = expandir(args.socios) if args.socios else []
    if not caminhos_estab or not caminhos_emp:
        print("Nenhum arquivo de Estabelecimentos ou Empresas encontrado. Confira os caminhos/padrões.", file=sys.stderr)
        sys.exit(1)

    print(f"segmento: {args.segmento}", file=sys.stderr)
    print(f"[1/4] escaneando {len(caminhos_estab)} arquivo(s) de estabelecimentos...", file=sys.stderr)
    accepted, estab_data = escanear_estabelecimentos(caminhos_estab, seg, positivas, args.limiar, args.incluir_so_keyword)
    print(f"      {len(accepted)} CNPJs aceitos até aqui", file=sys.stderr)

    print(f"[2/4] escaneando {len(caminhos_emp)} arquivo(s) de empresas (razão social + capital)...", file=sys.stderr)
    empresas, novos = escanear_empresas(caminhos_emp, accepted, positivas, args.incluir_so_keyword)
    if novos:
        print(f"      +{len(novos)} CNPJs novos via razão social — buscando dados de estabelecimento deles...", file=sys.stderr)
        _, estab_novos = escanear_estabelecimentos(
            caminhos_estab, seg, positivas, args.limiar, args.incluir_so_keyword, ids_extra=novos
        )
        for cnpj_basico, dados in estab_novos.items():
            estab_data.setdefault(cnpj_basico, dados)
        accepted |= novos

    leads = {c: d for c, d in estab_data.items() if c in empresas}
    sem_empresa = len(accepted - set(leads))
    if sem_empresa:
        print(f"[aviso] {sem_empresa} CNPJs aceitos sem estabelecimento ativo ou sem registro em Empresas — ignorados", file=sys.stderr)
    print(f"      candidatos: {len(leads)}", file=sys.stderr)

    # Exclusão hard de CNAE principal (cartório etc.)
    antes = len(leads)
    leads = {c: d for c, d in leads.items() if d.get("cnae_fiscal_principal", "").strip() not in seg["cnaes_excluir"]}
    if seg["cnaes_excluir"]:
        print(f"      exclusão de CNAE principal {sorted(seg['cnaes_excluir'])}: {antes} -> {len(leads)}", file=sys.stderr)

    # Keywords negativas (razão social + nome fantasia) e positivas (relevância)
    removidos_por_negativa: dict[str, int] = {}
    qtd_sem_keyword = 0
    filtrados = {}
    for cnpj_basico, dados in leads.items():
        texto = empresas[cnpj_basico]["razao_social"] + " " + dados.get("nome_fantasia", "")
        negs = termos_encontrados(texto, negativas)
        if negs:
            for n in negs:
                removidos_por_negativa[n] = removidos_por_negativa.get(n, 0) + 1
            continue
        poss = termos_encontrados(texto, positivas)
        if args.exigir_keyword and not poss:
            qtd_sem_keyword += 1
            continue
        dados["keywords_positivas"] = "|".join(poss)
        sinais = [s for s in dados["cnaes_sinal_encontrados"].split("|") if s]
        # 0..: +2 se tem keyword positiva, +1 por CNAE-sinal, +1 se veio por CNAE
        dados["relevancia"] = (2 if poss else 0) + len(sinais) + (1 if dados["motivo_match"] == "cnae" else 0)
        filtrados[cnpj_basico] = dados
    qtd_negativa = len(leads) - len(filtrados) - qtd_sem_keyword
    print(f"      keywords negativas: -{qtd_negativa}", file=sys.stderr)
    if args.exigir_keyword:
        print(f"      --exigir-keyword: -{qtd_sem_keyword} sem keyword positiva", file=sys.stderr)
    print(f"      leads após keywords: {len(filtrados)}", file=sys.stderr)
    if removidos_por_negativa:
        top = sorted(removidos_por_negativa.items(), key=lambda kv: -kv[1])
        print("      termos negativos mais frequentes: " + ", ".join(f"{t}={q}" for t, q in top[:10]), file=sys.stderr)
    leads = filtrados

    if args.capital_maximo is not None:
        antes = len(leads)
        leads = {c: d for c, d in leads.items() if empresas[c]["capital_social"] <= args.capital_maximo}
        print(f"      teto de capital social (<= {args.capital_maximo:,.2f}): {antes} -> {len(leads)}", file=sys.stderr)
    imprimir_distribuicao_capital([empresas[c]["capital_social"] for c in leads])

    decisores: dict[str, dict] = {}
    if caminhos_soc:
        print(f"[3/4] escaneando {len(caminhos_soc)} arquivo(s) de sócios (decisor provável)...", file=sys.stderr)
        decisores = escanear_socios(caminhos_soc, set(leads))
        print(f"      decisor encontrado para {len(decisores)}/{len(leads)} leads", file=sys.stderr)
    else:
        print("[3/4] --socios não informado, pulando identificação de decisor", file=sys.stderr)

    municipios = carregar_municipios(args.municipios)

    print(f"[4/4] gravando {args.saida}...", file=sys.stderr)
    ordenados = sorted(leads.items(), key=lambda kv: (-kv[1]["relevancia"], -kv[1]["percentual_match_cnae"], kv[0]))
    with open(args.saida, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=COLUNAS_SAIDA)
        writer.writeheader()
        for cnpj_basico, estab in ordenados:
            emp = empresas[cnpj_basico]
            decisor = decisores.get(cnpj_basico, {})
            writer.writerow(
                {
                    "segmento": args.segmento,
                    "cnpj": cnpj_basico + estab.get("cnpj_ordem", "") + estab.get("cnpj_dv", ""),
                    "razao_social": emp["razao_social"],
                    "nome_fantasia": estab.get("nome_fantasia", ""),
                    "relevancia": estab["relevancia"],
                    "keywords_positivas": estab["keywords_positivas"],
                    "cnaes_sinal_encontrados": estab["cnaes_sinal_encontrados"],
                    "percentual_match_cnae": estab.get("percentual_match_cnae", 0),
                    "qtd_cnaes_alvo_encontrados": estab.get("qtd_cnaes_alvo_encontrados", 0),
                    "motivo_match": estab.get("motivo_match", ""),
                    "cnae_fiscal_principal": estab.get("cnae_fiscal_principal", ""),
                    "cnae_fiscal_secundaria": estab.get("cnae_fiscal_secundaria", ""),
                    "data_inicio_atividade": estab.get("data_inicio_atividade", ""),
                    "capital_social": emp["capital_social"],
                    "porte_empresa": emp["porte_empresa"],
                    "natureza_juridica": emp["natureza_juridica"],
                    "uf": estab.get("uf", ""),
                    "municipio": estab.get("municipio", ""),
                    "nome_municipio": municipios.get((estab.get("municipio") or "").strip(), ""),
                    "ddd_1": estab.get("ddd_1", ""),
                    "telefone_1": estab.get("telefone_1", ""),
                    "ddd_2": estab.get("ddd_2", ""),
                    "telefone_2": estab.get("telefone_2", ""),
                    "correio_eletronico": estab.get("correio_eletronico", ""),
                    **{k: decisor.get(k, "") for k in ("nome_decisor", "documento_decisor", "qualificacao_socio", "eh_decisor", "data_entrada_sociedade")},
                }
            )
    print(f"CONCLUIDO: {len(ordenados)} leads", file=sys.stderr)


if __name__ == "__main__":
    main()
