"""Módulo 3 (parte 1) — Importação dos dados abertos do CNPJ (Receita Federal).

Fonte: mirror público dos dados abertos do CNPJ, atualizado mensalmente pela
própria Receita (o layout dos arquivos é o oficial "Leiaute de dados abertos
do CNPJ", estável há anos). Baixa e importa, nessa ordem:

  1. Municipios.zip / Qualificacoes.zip — tabelas pequenas de referência.
  2. Empresas0..9.zip  -> stg_empresas (razão social, capital, porte, natureza).
  3. Estabelecimentos0..9.zip -> stg_estabelecimentos (situação, endereço,
     telefone, e-mail, CNAE — isso é o grosso do volume, ~20 GB comprimidos).
  4. Socios0..9.zip -> stg_socios (QSA).
  5. JOIN final: estabelecimentos + empresas + municípios -> empresas_receita
     (CNPJ completo = cnpj_basico + cnpj_ordem + cnpj_dv).

IMPORTANTE sobre os índices 0-9: a Receita particiona os três datasets de
forma independente — o CNPJ básico X pode estar no Empresas3.zip e seu
estabelecimento correspondente no Estabelecimentos7.zip. Por isso os 10
arquivos de cada dataset são TODOS acumulados nas tabelas de staging antes
do JOIN final; nunca junte por índice de arquivo.

Testado nesta implementação com os arquivos reais (Municipios, Qualificacoes
e Empresas0 — ver notas no README). Estabelecimentos não foi baixado por
completo durante o desenvolvimento por ser muito grande (~2,2 GB por arquivo,
~20 GB no total): o layout usado aqui é o oficial e estável, mas VALIDE com
o "modo de teste" (importar só o índice 0) antes de agendar a importação
mensal completa.

Requisito de infraestrutura: espaço em disco generoso na VPS — o conjunto
completo (Empresas + Estabelecimentos + Sócios, todos os 10 arquivos de cada)
ocupa, na pior hipótese, bem mais de 100 GB entre download temporário e dados
já carregados no Postgres. Avalie o tamanho do disco antes de rodar em
produção.
"""
import subprocess
import time
from dataclasses import dataclass, field

import requests
from sqlalchemy import text

from app.db import execute, get_engine

BASE_URL_PADRAO = "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos"

# Qualificações de sócio tratadas como "administrador" pra fins de
# identificação do contato principal na empresa (usado no Módulo 6, Fase 3).
CODIGOS_QUALIFICACAO_ADMINISTRADOR = {
    "05", "08", "10", "16", "28", "34", "49", "50",
    "64", "65", "66", "67", "68", "70", "71", "72", "73", "74", "75", "78", "79",
}


@dataclass
class ResultadoImportacao:
    dataset: str
    arquivos_processados: int = 0
    linhas_importadas: int = 0
    erros: list[str] = field(default_factory=list)


def _descobrir_pasta_mais_recente(base_url: str = BASE_URL_PADRAO) -> str:
    """O mirror organiza os arquivos em subpastas por data (AAAA-MM-DD).
    Pega a mais recente automaticamente — é por isso que a importação
    mensal não exige editar nenhuma URL na configuração."""
    import re

    resp = requests.get(f"{base_url}/", timeout=30)
    resp.raise_for_status()
    datas = sorted(set(re.findall(r'href="(\d{4}-\d{2}-\d{2})/"', resp.text)))
    if not datas:
        raise RuntimeError("Não foi possível encontrar nenhuma pasta de dados no mirror da Receita.")
    return datas[-1]


def _copiar_zip_para_staging(url: str, tabela_staging: str, num_colunas: int) -> int:
    """Baixa um .zip e faz COPY direto pro Postgres via pipe, sem guardar
    o CSV descomprimido em disco (só o .zip temporário)."""
    caminho_zip = "/tmp/_leads_pj_download.zip"

    with requests.get(url, stream=True, timeout=600) as resp:
        resp.raise_for_status()
        with open(caminho_zip, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    colunas_sql = ", ".join(f"c{i}" for i in range(num_colunas))  # não usado; COPY usa ordem da tabela
    engine = get_engine()
    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        processo = subprocess.Popen(["unzip", "-p", caminho_zip], stdout=subprocess.PIPE)
        cursor.copy_expert(
            f"COPY {tabela_staging} FROM STDIN WITH (FORMAT csv, DELIMITER ';', QUOTE '\"', ENCODING 'latin1')",
            processo.stdout,
        )
        processo.wait()
        raw_conn.commit()
        cursor.execute(f"SELECT count(*) FROM {tabela_staging}")
        total = cursor.fetchone()[0]
    finally:
        raw_conn.close()

    import os

    os.remove(caminho_zip)
    return total


def importar_referencias(base_url: str = BASE_URL_PADRAO) -> None:
    pasta = _descobrir_pasta_mais_recente(base_url)
    execute("TRUNCATE municipios_rf")
    execute("TRUNCATE qualificacoes_rf")
    _copiar_zip_para_staging(f"{base_url}/{pasta}/Municipios.zip", "municipios_rf", 2)
    _copiar_zip_para_staging(f"{base_url}/{pasta}/Qualificacoes.zip", "qualificacoes_rf", 2)


def importar_empresas(indices: list[int] | None = None, base_url: str = BASE_URL_PADRAO) -> ResultadoImportacao:
    indices = indices if indices is not None else list(range(10))
    pasta = _descobrir_pasta_mais_recente(base_url)
    resultado = ResultadoImportacao(dataset="empresas")
    execute("TRUNCATE stg_empresas")
    for i in indices:
        try:
            total = _copiar_zip_para_staging(f"{base_url}/{pasta}/Empresas{i}.zip", "stg_empresas", 7)
            resultado.arquivos_processados += 1
            resultado.linhas_importadas = total
        except Exception as exc:  # noqa: BLE001
            resultado.erros.append(f"Empresas{i}.zip: {exc}")
    return resultado


def importar_estabelecimentos(indices: list[int] | None = None, base_url: str = BASE_URL_PADRAO) -> ResultadoImportacao:
    indices = indices if indices is not None else list(range(10))
    pasta = _descobrir_pasta_mais_recente(base_url)
    resultado = ResultadoImportacao(dataset="estabelecimentos")
    execute("TRUNCATE stg_estabelecimentos")
    for i in indices:
        try:
            total = _copiar_zip_para_staging(
                f"{base_url}/{pasta}/Estabelecimentos{i}.zip", "stg_estabelecimentos", 30
            )
            resultado.arquivos_processados += 1
            resultado.linhas_importadas = total
        except Exception as exc:  # noqa: BLE001
            resultado.erros.append(f"Estabelecimentos{i}.zip: {exc}")
    return resultado


def importar_socios(indices: list[int] | None = None, base_url: str = BASE_URL_PADRAO) -> ResultadoImportacao:
    indices = indices if indices is not None else list(range(10))
    pasta = _descobrir_pasta_mais_recente(base_url)
    resultado = ResultadoImportacao(dataset="socios")
    execute("TRUNCATE stg_socios")
    for i in indices:
        try:
            total = _copiar_zip_para_staging(f"{base_url}/{pasta}/Socios{i}.zip", "stg_socios", 11)
            resultado.arquivos_processados += 1
            resultado.linhas_importadas = total
        except Exception as exc:  # noqa: BLE001
            resultado.erros.append(f"Socios{i}.zip: {exc}")
    return resultado


def consolidar_empresas_receita() -> int:
    """JOIN final: stg_estabelecimentos + stg_empresas + municipios_rf ->
    empresas_receita (tabela que as telas e a qualificação usam). Faz
    upsert (ON CONFLICT) pra permitir reprocessamento sem duplicar."""
    sql = """
        INSERT INTO empresas_receita (
            cnpj, cnpj_basico, razao_social, nome_fantasia, natureza_juridica_codigo,
            situacao_cadastral, data_situacao_cadastral, data_inicio_atividade,
            capital_social, porte_codigo, cnae_principal, uf, municipio,
            logradouro, telefone, email, atualizado_em
        )
        SELECT
            e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv AS cnpj,
            e.cnpj_basico,
            emp.razao_social,
            e.nome_fantasia,
            NULLIF(emp.natureza_juridica_codigo, '')::INTEGER,
            CASE e.situacao_cadastral
                WHEN '01' THEN 'NULA'
                WHEN '02' THEN 'ATIVA'
                WHEN '03' THEN 'SUSPENSA'
                WHEN '04' THEN 'INAPTA'
                WHEN '08' THEN 'BAIXADA'
                ELSE e.situacao_cadastral
            END,
            to_date(NULLIF(e.data_situacao_cadastral, ''), 'YYYYMMDD'),
            to_date(NULLIF(e.data_inicio_atividade, ''), 'YYYYMMDD'),
            NULLIF(REPLACE(emp.capital_social, ',', '.'), '')::NUMERIC,
            emp.porte_codigo,
            e.cnae_fiscal_principal,
            e.uf,
            m.nome,
            TRIM(COALESCE(e.tipo_logradouro, '') || ' ' || COALESCE(e.logradouro, '') || ', ' || COALESCE(e.numero, '')),
            CASE WHEN e.ddd1 <> '' AND e.telefone1 <> '' THEN e.ddd1 || e.telefone1 ELSE NULL END,
            NULLIF(e.correio_eletronico, ''),
            now()
        FROM stg_estabelecimentos e
        JOIN stg_empresas emp ON emp.cnpj_basico = e.cnpj_basico
        LEFT JOIN municipios_rf m ON m.codigo = e.municipio_codigo
        ON CONFLICT (cnpj) DO UPDATE SET
            razao_social = EXCLUDED.razao_social,
            nome_fantasia = EXCLUDED.nome_fantasia,
            situacao_cadastral = EXCLUDED.situacao_cadastral,
            data_situacao_cadastral = EXCLUDED.data_situacao_cadastral,
            capital_social = EXCLUDED.capital_social,
            porte_codigo = EXCLUDED.porte_codigo,
            cnae_principal = EXCLUDED.cnae_principal,
            uf = EXCLUDED.uf,
            municipio = EXCLUDED.municipio,
            logradouro = EXCLUDED.logradouro,
            telefone = EXCLUDED.telefone,
            email = EXCLUDED.email,
            atualizado_em = now()
    """
    with get_engine().begin() as conn:
        resultado = conn.execute(text(sql))
        return resultado.rowcount or 0


def consolidar_filiais_count() -> None:
    execute("TRUNCATE empresas_filiais_count")
    execute(
        """
        INSERT INTO empresas_filiais_count (cnpj_basico, total_filiais_ativas)
        SELECT cnpj_basico, count(*)
        FROM stg_estabelecimentos
        WHERE identificador_matriz_filial = '2' AND situacao_cadastral = '02'
        GROUP BY cnpj_basico
        """
    )


def consolidar_socios() -> None:
    execute("TRUNCATE socios")
    execute(
        f"""
        INSERT INTO socios (cnpj_basico, nome_socio, cpf_mascarado, qualificacao, administrador)
        SELECT
            s.cnpj_basico,
            s.nome_socio,
            s.cpf_cnpj_socio,
            q.nome,
            s.qualificacao_socio IN ({",".join(f"'{c}'" for c in CODIGOS_QUALIFICACAO_ADMINISTRADOR)})
        FROM stg_socios s
        LEFT JOIN qualificacoes_rf q ON q.codigo = s.qualificacao_socio
        """
    )


def executar_importacao_completa(indices: list[int] | None = None) -> list[ResultadoImportacao]:
    """Roda a importação mensal inteira, nessa ordem. Pensado pra ser
    chamado pelo agendador ou pelo botão "Rodar agora" (Fase 1: Receita;
    PGFN é uma rotina separada, ver pgfn_import.py)."""
    resultados = []
    importar_referencias()

    resultados.append(importar_empresas(indices))
    resultados.append(importar_estabelecimentos(indices))
    resultados.append(importar_socios(indices))

    consolidados = consolidar_empresas_receita()
    consolidar_filiais_count()
    consolidar_socios()

    resultados.append(ResultadoImportacao(dataset="consolidacao_final", linhas_importadas=consolidados))
    return resultados
