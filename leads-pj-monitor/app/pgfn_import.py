"""Módulo 3 (parte 4) — Importação da dívida ativa da PGFN (dados abertos).

Fonte: https://dadosabertos.pgfn.gov.br/, organizada em pastas por trimestre
(AAAA_trimestre_NN), cada uma com 3 arquivos: FGTS, Não Previdenciário e
Previdenciário. Layout confirmado contra o arquivo real (trimestre mais
recente disponível no momento do desenvolvimento):

- Cada .zip contém, internamente, vários .csv (o número e os nomes variam
  por trimestre — por isso os nomes internos são descobertos dinamicamente,
  nunca hardcoded).
- CSV com cabeçalho, separador ";", codificação latin1, sem aspas nos campos.
- Colunas: CPF_CNPJ;TIPO_PESSOA;TIPO_DEVEDOR;NOME_DEVEDOR;UF_DEVEDOR;
  UNIDADE_RESPONSAVEL;NUMERO_INSCRICAO;TIPO_SITUACAO_INSCRICAO;
  SITUACAO_INSCRICAO;RECEITA_PRINCIPAL;DATA_INSCRICAO;INDICADOR_AJUIZADO;
  VALOR_CONSOLIDADO
- CPF_CNPJ vem formatado (com pontuação) e o CPF de pessoa física vem
  parcialmente mascarado — só pessoa jurídica é aproveitada aqui.
- VALOR_CONSOLIDADO usa ponto como separador decimal (diferente dos
  arquivos da Receita, que usam vírgula!).

É normal esse import levar bastante tempo (os 3 arquivos juntos passam de
9 GB descomprimidos) — por isso é uma rotina trimestral separada da coleta
diária, com botão próprio na tela de Configurações.
"""
import io
import subprocess
import zipfile
from dataclasses import dataclass, field

import requests
from sqlalchemy import text

from app.db import execute, get_engine

BASE_URL_PADRAO = "https://dadosabertos.pgfn.gov.br"
ARQUIVOS_POR_TRIMESTRE = [
    "Dados_abertos_Nao_Previdenciario.zip",
    "Dados_abertos_Previdenciario.zip",
    "Dados_abertos_FGTS.zip",
]


@dataclass
class ResultadoImportacaoPgfn:
    trimestre: str
    arquivos_processados: list[str] = field(default_factory=list)
    linhas_importadas: int = 0
    erros: list[str] = field(default_factory=list)


def _descobrir_trimestre_mais_recente(base_url: str = BASE_URL_PADRAO) -> str:
    import re

    resp = requests.get(f"{base_url}/", timeout=30)
    resp.raise_for_status()
    pastas = sorted(set(re.findall(r'href="(\d{4}_trimestre_\d{2})/"', resp.text)))
    if not pastas:
        raise RuntimeError("Não foi possível encontrar nenhum trimestre no portal de dados abertos da PGFN.")
    return pastas[-1]


def _nome_para_referencia(pasta_trimestre: str) -> str:
    # "2026_trimestre_02" -> "2026-Q2"
    ano, _, numero = pasta_trimestre.split("_")
    return f"{ano}-Q{int(numero)}"


def _listar_entradas_zip_remoto(url: str) -> list[str]:
    """Baixa só o suficiente pra ler a lista de arquivos dentro do .zip
    (a central directory fica no final do arquivo) — como esses .zip são
    enormes, baixar tudo só pra listar seria desperdício; na prática ainda
    baixamos o arquivo inteiro antes de processar (ver _processar_zip), mas
    isso aqui é usado só no modo de teste/diagnóstico."""
    with requests.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        dados = resp.content
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        return z.namelist()


def _processar_zip(url: str, tabela_staging: str) -> tuple[list[str], int]:
    caminho_zip = "/tmp/_leads_pj_pgfn_download.zip"
    with requests.get(url, stream=True, timeout=1800) as resp:
        resp.raise_for_status()
        with open(caminho_zip, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    with zipfile.ZipFile(caminho_zip) as z:
        entradas = [n for n in z.namelist() if n.lower().endswith(".csv")]

    engine = get_engine()
    raw_conn = engine.raw_connection()
    total_linhas = 0
    try:
        cursor = raw_conn.cursor()
        for entrada in entradas:
            processo = subprocess.Popen(
                ["unzip", "-p", caminho_zip, entrada], stdout=subprocess.PIPE
            )
            # Mesmo cuidado do import da Receita: remove byte nulo do fluxo
            # antes do COPY (o Postgres rejeita texto com 0x00 em qualquer
            # codificação).
            sanitiza = subprocess.Popen(["tr", "-d", "\\000"], stdin=processo.stdout, stdout=subprocess.PIPE)
            processo.stdout.close()
            cursor.copy_expert(
                f"COPY {tabela_staging} FROM STDIN WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'latin1')",
                sanitiza.stdout,
            )
            processo.wait()
            sanitiza.wait()
        raw_conn.commit()
        cursor.execute(f"SELECT count(*) FROM {tabela_staging}")
        total_linhas = cursor.fetchone()[0]
    finally:
        raw_conn.close()

    import os

    os.remove(caminho_zip)
    return entradas, total_linhas


def consolidar_pgfn(referencia_trimestre: str) -> int:
    """Consolida as duas staging (stg_pgfn: Não Previdenciário + Previdenciário;
    stg_pgfn_fgts: FGTS, layout com 2 colunas extras) somando a dívida de
    cada CNPJ entre as duas fontes."""
    sql = """
        INSERT INTO pgfn_divida_ativa (cnpj, valor_consolidado, tipo_devedor, situacao, referencia_trimestre)
        SELECT cnpj, SUM(valor), MIN(tipo_devedor), MIN(situacao), :referencia
        FROM (
            SELECT
                regexp_replace(cpf_cnpj, '\\D', '', 'g') AS cnpj,
                NULLIF(valor_consolidado, '')::NUMERIC AS valor,
                tipo_devedor, situacao_inscricao AS situacao
            FROM stg_pgfn
            WHERE tipo_pessoa ILIKE '%jur%'
              AND length(regexp_replace(cpf_cnpj, '\\D', '', 'g')) = 14

            UNION ALL

            SELECT
                regexp_replace(cpf_cnpj, '\\D', '', 'g') AS cnpj,
                NULLIF(valor_consolidado, '')::NUMERIC AS valor,
                tipo_devedor, situacao_inscricao AS situacao
            FROM stg_pgfn_fgts
            WHERE tipo_pessoa ILIKE '%jur%'
              AND length(regexp_replace(cpf_cnpj, '\\D', '', 'g')) = 14
        ) tudo
        GROUP BY cnpj
        ON CONFLICT (cnpj, referencia_trimestre) DO UPDATE SET
            valor_consolidado = EXCLUDED.valor_consolidado,
            tipo_devedor = EXCLUDED.tipo_devedor,
            situacao = EXCLUDED.situacao,
            atualizado_em = now()
    """
    with get_engine().begin() as conn:
        resultado = conn.execute(text(sql), {"referencia": referencia_trimestre})
        return resultado.rowcount or 0


def executar_importacao_pgfn(base_url: str = BASE_URL_PADRAO) -> ResultadoImportacaoPgfn:
    pasta = _descobrir_trimestre_mais_recente(base_url)
    referencia = _nome_para_referencia(pasta)
    resultado = ResultadoImportacaoPgfn(trimestre=referencia)

    execute("TRUNCATE stg_pgfn")
    execute("TRUNCATE stg_pgfn_fgts")

    for nome_arquivo in ARQUIVOS_POR_TRIMESTRE:
        tabela = "stg_pgfn_fgts" if "FGTS" in nome_arquivo else "stg_pgfn"
        url = f"{base_url}/{pasta}/{nome_arquivo}"
        try:
            entradas, total = _processar_zip(url, tabela)
            resultado.arquivos_processados.append(f"{nome_arquivo} ({len(entradas)} csv internos)")
            resultado.linhas_importadas = total
        except Exception as exc:  # noqa: BLE001
            resultado.erros.append(f"{nome_arquivo}: {exc}")

    consolidados = consolidar_pgfn(referencia)
    resultado.linhas_importadas = consolidados
    return resultado
