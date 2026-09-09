"""
Filtra bases brutas de CNPJ por CNAE + palavras-chave de fintech, identifica
o decisor provável de cada empresa e gera leads_com_socios.csv — tudo
localmente, sem precisar de BigQuery/basedosdados.

Alternativa a filtro_cnae.sql + socios_decisor.sql + join_leads_socios.sql
para quem não tem (ou não quer usar) acesso a um projeto Google Cloud: você
baixa os arquivos abertos da Receita Federal (https://dados.gov.br, ou
https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/) e roda
este script sobre eles.

Aceita três formatos de entrada (detecta automaticamente por arquivo):
  - Arquivos .zip originais da Receita Federal, exatamente como distribuídos
    (ex.: Empresas0.zip) — lidos por streaming direto de dentro do zip, sem
    extrair pra disco.
  - Os mesmos arquivos já descompactados: sem cabeçalho, separados por ';',
    codificação Latin-1, divididos em várias partes (0..9).
  - CSVs com cabeçalho (ex.: exportados do basedosdados ou do BigQuery),
    separados por ',' ou ';', UTF-8.

Cada um dos três tipos de arquivo pode vir dividido em várias partes —
passe múltiplos caminhos ou um padrão glob (entre aspas, para o shell não
expandir antes do Python).

Uso:
    python filtrar_cnpjs_local.py \
      --estabelecimentos "dados/*ESTABELE*" \
      --empresas "dados/*EMPRECSV*" \
      --socios "dados/*SOCIOCSV*" \
      --saida leads_com_socios.csv

--socios é opcional: sem ele, a saída não tem colunas de decisor (nome_decisor
etc. ficam ausentes, e a etapa de IA simplesmente não usa esse contexto).

Regra de filtro (igual à de filtro_cnae.sql, mais abrangente):
  entra no resultado todo CNPJ ativo cujo estabelecimento tenha PELO MENOS
  60% dos 7 CNAEs alvo (principal + secundários), OU cuja razão social ou
  nome fantasia contenha alguma palavra-chave de fintech (lista em
  PALAVRAS_CHAVE_FINTECH, editável abaixo).

Aviso de performance: para as bases nacionais completas (dezenas de milhões
de linhas), este script processa tudo por streaming (linha a linha, sem
carregar os arquivos inteiros em memória), mas ainda assim pode levar
minutos a algumas horas dependendo do hardware. Para volumes muito grandes
com filtros recorrentes, DuckDB ou a rota via BigQuery (filtro_cnae.sql)
tendem a ser mais rápidos.
"""

import argparse
import csv
import glob
import io
import sys
import unicodedata
import zipfile
from typing import Iterable, Iterator

CNAES_ALVO = {
    "7490104",  # Principal: intermediação e agenciamento de serviços e negócios em geral
    "6203100",  # Desenvolvimento e licenciamento de programas de computador não-customizáveis
    "7020400",  # Consultoria em gestão empresarial
    "6201501",  # Desenvolvimento de programas de computador sob encomenda
    "8299799",  # Outras atividades de serviços prestados às empresas
    "6209100",  # Suporte técnico, manutenção e outros serviços em TI
    "6619399",  # Outras atividades auxiliares dos serviços financeiros
}
LIMIAR_PADRAO = 0.6

# Editável: termos comuns em razão social / nome fantasia de fintechs
# brasileiras. Comparação é feita sem acento e em minúsculas (ver normaliza()).
#
# Termos como "banco", "credito", "financeira", "investimento" e "corretora"
# foram DELIBERADAMENTE excluídos: testados contra a base real, cada um
# sozinho gerava milhares de falsos positivos (fundos de investimento,
# holdings "XYZ Investimentos Ltda", corretoras de seguros tradicionais,
# consultorias financeiras) que não são fintechs. Prefira uma lista mais
# curta e precisa — o filtro por CNAE já cobre a maior parte do universo
# real; a lista de palavras-chave é só um complemento para pegar fintechs
# óbvias pelo nome que não bateram o limiar de CNAE.
PALAVRAS_CHAVE_FINTECH = [
    "fintech",
    "pagamento",
    "pagamentos",
    "pay",
    "payment",
    "payments",
    "banking",
    "solucoes financeiras",
    "solucao financeira",
    "solucoes de pagamento",
    "solucao de pagamento",
    "carteira digital",
    "wallet",
    "meios de pagamento",
    "adquirencia",
    "cripto",
    "criptomoeda",
    "factoring",
    "antecipacao de recebiveis",
]

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

COLUNAS_SAIDA = [
    "cnpj", "razao_social", "nome_fantasia", "percentual_match_cnae",
    "qtd_cnaes_alvo_encontrados", "motivo_match", "cnae_fiscal_principal",
    "cnae_fiscal_secundaria", "data_inicio_atividade", "uf", "municipio",
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
    texto = texto or ""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return texto.lower()


def contem_palavra_chave(texto: str) -> bool:
    t = normaliza(texto)
    return any(p in t for p in PALAVRAS_CHAVE_FINTECH)


def percentual_cnae(principal: str, secundaria: str) -> tuple[float, int]:
    todos = set()
    if principal:
        todos.add(principal.strip())
    if secundaria:
        todos.update(s.strip() for s in secundaria.split(",") if s.strip())
    qtd = len(todos & CNAES_ALVO)
    return qtd / len(CNAES_ALVO), qtd


def _linhas_de_bufferedreader(f_bin, campos_padrao: list[str]) -> Iterator[dict]:
    """Recebe um stream binário (arquivo aberto ou membro de zip), detecta
    encoding/delimitador/cabeçalho por uma amostra do início do arquivo, e
    devolve um iterador de dicts com nomes de coluna padronizados
    (CAMPOS_*)."""
    # Amostra grande (não só a 1a linha): um arquivo Latin-1 pode ter dezenas
    # de linhas puramente ASCII antes do primeiro acento aparecer, o que
    # faria uma amostra pequena decodificar "com sucesso" como UTF-8 por
    # engano e quebrar mais adiante no arquivo.
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
    """Abre um arquivo de dados (.zip original da Receita, ou CSV já
    descompactado) e devolve um iterador de dicts."""
    if caminho.lower().endswith(".zip"):
        with zipfile.ZipFile(caminho) as z:
            membros = [n for n in z.namelist() if not n.endswith("/")]
            for nome in membros:
                with z.open(nome) as f_bin:
                    yield from _linhas_de_bufferedreader(f_bin, campos_padrao)
    else:
        with open(caminho, "rb") as f_bin:
            yield from _linhas_de_bufferedreader(f_bin, campos_padrao)


def escanear_estabelecimentos(
    caminhos: list[str], limiar: float, ids_extra: Iterable[str] = ()
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

            pct, qtd = percentual_cnae(row.get("cnae_fiscal_principal", ""), row.get("cnae_fiscal_secundaria", ""))
            match_cnae = pct >= limiar
            match_nome = contem_palavra_chave(row.get("nome_fantasia", ""))
            ja_aceito = cnpj_basico in accepted

            if not (match_cnae or match_nome or ja_aceito):
                continue

            accepted.add(cnpj_basico)
            motivo = "cnae" if match_cnae else ("nome_fantasia" if match_nome else "razao_social")
            atual = estab_data.get(cnpj_basico)
            eh_matriz = row.get("identificador_matriz_filial") == "1"
            if atual is None or (eh_matriz and not atual.get("_eh_matriz")):
                estab_data[cnpj_basico] = {
                    **row,
                    "percentual_match_cnae": round(pct, 2),
                    "qtd_cnaes_alvo_encontrados": qtd,
                    "motivo_match": motivo,
                    "_eh_matriz": eh_matriz,
                }
    return accepted, estab_data


def escanear_empresas(caminhos: list[str], accepted_estab_ids: set[str]) -> tuple[dict[str, str], set[str]]:
    razao_social_map: dict[str, str] = {}
    novos_via_razao_social: set[str] = set()
    for caminho in caminhos:
        for row in abrir_linhas(caminho, CAMPOS_EMPRESAS):
            cnpj_basico = (row.get("cnpj_basico") or "").strip()
            if not cnpj_basico:
                continue
            razao = row.get("razao_social", "")
            match_razao = contem_palavra_chave(razao)
            if cnpj_basico in accepted_estab_ids or match_razao:
                razao_social_map[cnpj_basico] = razao
                if match_razao and cnpj_basico not in accepted_estab_ids:
                    novos_via_razao_social.add(cnpj_basico)
    return razao_social_map, novos_via_razao_social


def escanear_socios(caminhos: list[str], accepted_ids: set[str]) -> dict[str, dict]:
    melhor: dict[str, tuple] = {}
    for caminho in caminhos:
        for row in abrir_linhas(caminho, CAMPOS_SOCIOS):
            cnpj_basico = (row.get("cnpj_basico") or "").strip()
            if cnpj_basico not in accepted_ids:
                continue
            qualificacao = row.get("qualificacao_socio", "")
            eh_decisor = qualificacao in QUALIFICACOES_DECISOR
            rank = (0 if eh_decisor else 1, row.get("data_entrada_sociedade", "99999999"))
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--estabelecimentos", nargs="+", required=True, help="Caminho(s)/padrão(ões) glob dos arquivos de Estabelecimentos")
    parser.add_argument("--empresas", nargs="+", required=True, help="Caminho(s)/padrão(ões) glob dos arquivos de Empresas")
    parser.add_argument("--socios", nargs="+", default=None, help="Caminho(s)/padrão(ões) glob dos arquivos de Sócios (opcional)")
    parser.add_argument("--saida", required=True, help="CSV de saída (leads_com_socios.csv)")
    parser.add_argument("--limiar", type=float, default=LIMIAR_PADRAO, help=f"Percentual mínimo de CNAEs alvo (padrão {LIMIAR_PADRAO})")
    args = parser.parse_args()

    caminhos_estab = expandir(args.estabelecimentos)
    caminhos_emp = expandir(args.empresas)
    caminhos_soc = expandir(args.socios) if args.socios else []

    if not caminhos_estab or not caminhos_emp:
        print("Nenhum arquivo de Estabelecimentos ou Empresas encontrado. Confira os caminhos/padrões.", file=sys.stderr)
        sys.exit(1)

    print(f"[1/4] escaneando {len(caminhos_estab)} arquivo(s) de estabelecimentos (CNAE + nome fantasia)...", file=sys.stderr)
    accepted, estab_data = escanear_estabelecimentos(caminhos_estab, args.limiar)
    print(f"      {len(accepted)} CNPJs aceitos até aqui", file=sys.stderr)

    print(f"[2/4] escaneando {len(caminhos_emp)} arquivo(s) de empresas (razão social + join)...", file=sys.stderr)
    razao_social_map, novos = escanear_empresas(caminhos_emp, accepted)
    if novos:
        print(f"      +{len(novos)} CNPJs novos via razão social — buscando dados de estabelecimento deles...", file=sys.stderr)
        _, estab_data_novos = escanear_estabelecimentos(caminhos_estab, args.limiar, ids_extra=novos)
        estab_data.update(estab_data_novos)
        accepted |= novos

    leads = {cnpj_basico: dados for cnpj_basico, dados in estab_data.items() if cnpj_basico in razao_social_map}
    ignorados = accepted - set(leads)
    if ignorados:
        print(f"[aviso] {len(ignorados)} CNPJs aceitos não tinham registro correspondente em Empresas — ignorados", file=sys.stderr)
    print(f"      total de leads filtrados: {len(leads)}", file=sys.stderr)

    decisores: dict[str, dict] = {}
    if caminhos_soc:
        print(f"[3/4] escaneando {len(caminhos_soc)} arquivo(s) de sócios (decisor provável)...", file=sys.stderr)
        decisores = escanear_socios(caminhos_soc, set(leads))
        print(f"      decisor encontrado para {len(decisores)}/{len(leads)} leads", file=sys.stderr)
    else:
        print("[3/4] --socios não informado, pulando identificação de decisor", file=sys.stderr)

    print(f"[4/4] gravando {args.saida}...", file=sys.stderr)
    with open(args.saida, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=COLUNAS_SAIDA)
        writer.writeheader()
        for cnpj_basico, estab in sorted(leads.items(), key=lambda kv: -kv[1]["percentual_match_cnae"]):
            decisor = decisores.get(cnpj_basico, {})
            writer.writerow(
                {
                    "cnpj": cnpj_basico + estab.get("cnpj_ordem", "") + estab.get("cnpj_dv", ""),
                    "razao_social": razao_social_map.get(cnpj_basico, ""),
                    "nome_fantasia": estab.get("nome_fantasia", ""),
                    "percentual_match_cnae": estab.get("percentual_match_cnae", 0),
                    "qtd_cnaes_alvo_encontrados": estab.get("qtd_cnaes_alvo_encontrados", 0),
                    "motivo_match": estab.get("motivo_match", ""),
                    "cnae_fiscal_principal": estab.get("cnae_fiscal_principal", ""),
                    "cnae_fiscal_secundaria": estab.get("cnae_fiscal_secundaria", ""),
                    "data_inicio_atividade": estab.get("data_inicio_atividade", ""),
                    "uf": estab.get("uf", ""),
                    "municipio": estab.get("municipio", ""),
                    "ddd_1": estab.get("ddd_1", ""),
                    "telefone_1": estab.get("telefone_1", ""),
                    "ddd_2": estab.get("ddd_2", ""),
                    "telefone_2": estab.get("telefone_2", ""),
                    "correio_eletronico": estab.get("correio_eletronico", ""),
                    "nome_decisor": decisor.get("nome_decisor", ""),
                    "documento_decisor": decisor.get("documento_decisor", ""),
                    "qualificacao_socio": decisor.get("qualificacao_socio", ""),
                    "eh_decisor": decisor.get("eh_decisor", ""),
                    "data_entrada_sociedade": decisor.get("data_entrada_sociedade", ""),
                }
            )
    print("CONCLUIDO", file=sys.stderr)


if __name__ == "__main__":
    main()
