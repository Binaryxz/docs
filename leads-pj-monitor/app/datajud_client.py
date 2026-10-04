"""Módulo 1 — Descoberta de processos novos via API Pública do DataJud (CNJ).

Formato confirmado com chamadas reais contra a API (chave pública de
demonstração do CNJ): endpoint Elasticsearch em POST
https://api-publica.datajud.cnj.jus.br/{alias}/_search, autenticação via
header "Authorization: APIKey <chave>", corpo em DSL do Elasticsearch,
paginação por "search_after". Campos confirmados em "_source": numeroProcesso,
classe{codigo,nome}, assuntos[{codigo,nome}], dataAjuizamento, grau,
orgaoJulgador{codigo,nome,codigoMunicipioIBGE}, nivelSigilo (0 = público),
movimentos[...]. "valorCausa" e "partes" NÃO vieram no teste real (pelo menos
pra Juizado Especial Cível do TJSP) — ficam None/vazio quando ausentes, sem
quebrar nada; "partes" é por isso mesmo que o Módulo 2 (raspagem por
tribunal) existe.

**Achado importante testando ao vivo**: essa API é LENTA — mesmo uma
consulta simples (size=1, sem filtro) levou ~36-60s. Isso não parece ser
específico da chave pública compartilhada (autenticação errada responde
rápido) — é característica da API sob carga. Por isso o timeout de
requisição é bem mais generoso que o usual (ver TIMEOUT_REQUISICAO_SEGUNDOS)
e o retry/backoff é essencial: espere páginas ocasionalmente levarem mais de
um minuto.

**Dois bugs reais de verdade, só visíveis testando contra a API ao vivo**
(nenhum dos dois aparecia num teste sintético, por mais cuidadoso que
fosse — a API simplesmente não documenta isso):

1. Sort por "_id" como critério de desempate é **rejeitado** por este
   cluster ("Fielddata access on the _id field is disallowed") — erro 400,
   não timeout. "numeroProcesso" sozinho também falha (é "text", sem
   fielddata); o sub-campo "numeroProcesso.keyword" é o que funciona de
   verdade (testado, HTTP 200).
2. "dataAjuizamento" não é um campo "date" do Elasticsearch, é texto no
   formato compacto "yyyyMMddHHmmss" (ex: "20260730012658") — e um filtro
   "range/gte" em formato ISO ("2026-09-27") **não filtra nada**: testei
   com "gte: 2099-01-01" (data impossível) e voltou o total inteiro do
   índice, sem filtrar. Só filtra de verdade usando o mesmo formato
   compacto como valor do "gte". `_extrair_campos` converte o valor pra
   datetime antes de gravar no Postgres (gravar a string bruta direto
   quebraria o cast pra TIMESTAMPTZ); `coletar_tribunal` monta o "gte" já
   nesse formato.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from app.config import get_config
from app.db import execute, fetch_one, query_df
from app.tribunais import uf_do_tribunal

BASE_URL = "https://api-publica.datajud.cnj.jus.br"
TAMANHO_PAGINA = 100
MAX_TENTATIVAS = 5
# Visto em teste real: uma consulta com filtro de data + sort (o padrão da
# paginação) levou mais de 60s e ainda voltou 504. 120s dá margem real.
TIMEOUT_REQUISICAO_SEGUNDOS = 120


@dataclass
class ResultadoColeta:
    tribunal: str
    processos_novos: int = 0
    paginas_lidas: int = 0
    erros: list[str] = field(default_factory=list)


def _cursor_salvo(tribunal: str) -> list | None:
    row = fetch_one(
        "SELECT valor_json FROM config_kv WHERE chave = :chave",
        {"chave": f"datajud_cursor_{tribunal}"},
    )
    return row["valor_json"] if row else None


def _salvar_cursor(tribunal: str, search_after: list) -> None:
    execute(
        """
        INSERT INTO config_kv (chave, valor_json, atualizado_em)
        VALUES (:chave, CAST(:valor AS JSONB), now())
        ON CONFLICT (chave) DO UPDATE SET valor_json = CAST(:valor AS JSONB), atualizado_em = now()
        """,
        {"chave": f"datajud_cursor_{tribunal}", "valor": _json_seguro(search_after)},
    )


def _json_seguro(valor) -> str:
    import json

    return json.dumps(valor)


def _montar_query(classes_codigos: list[int], assuntos_codigos: list[int], data_minima: str) -> dict:
    deve = []
    if classes_codigos:
        deve.append({"terms": {"classe.codigo": classes_codigos}})
    if assuntos_codigos:
        deve.append({"terms": {"assuntos.codigo": assuntos_codigos}})

    filtro_data = {"range": {"dataAjuizamento": {"gte": data_minima}}}

    query = {"bool": {"filter": [filtro_data]}}
    if deve:
        query["bool"]["must"] = deve
    return query


def _requisitar_pagina(
    tribunal: str, api_key: str, query: dict, search_after: list | None
) -> dict:
    url = f"{BASE_URL}/{tribunal}/_search"
    corpo = {
        "size": TAMANHO_PAGINA,
        "query": query,
        # "_id" como critério de desempate é REJEITADO por este cluster
        # ("Fielddata access on the _id field is disallowed") — confirmado
        # em teste real (erro 400, não timeout). "numeroProcesso" sozinho
        # também falha (é "text", sem fielddata) — o sub-campo ".keyword"
        # é a forma correta e testada que funciona.
        "sort": [{"dataAjuizamento": "asc"}, {"numeroProcesso.keyword": "asc"}],
    }
    if search_after:
        corpo["search_after"] = search_after

    headers = {
        "Authorization": f"APIKey {api_key}",
        "Content-Type": "application/json",
    }

    ultimo_erro = None
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            resp = requests.post(url, json=corpo, headers=headers, timeout=TIMEOUT_REQUISICAO_SEGUNDOS)
            if resp.status_code == 429:
                espera = min(2**tentativa, 60)
                time.sleep(espera)
                continue
            if resp.status_code >= 500:
                ultimo_erro = f"HTTP {resp.status_code}: {resp.text[:300]}"
                time.sleep(min(2**tentativa, 30))
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            ultimo_erro = str(exc)
            time.sleep(min(2**tentativa, 30))
    raise RuntimeError(f"Falha ao consultar DataJud ({tribunal}) após {MAX_TENTATIVAS} tentativas: {ultimo_erro}")


def _nivel_sigilo_eh_publico(hit_source: dict) -> bool:
    # Convenção usual do DataJud: nivelSigilo == 0 é público. Processos em
    # segredo de justiça não devem ser processados — ficam de fora por
    # segurança caso o campo não venha (None é tratado como "não é público
    # com certeza" só se vier um valor explícito diferente de 0/ausente).
    nivel = hit_source.get("nivelSigilo")
    return nivel in (None, 0)


def _parse_data_ajuizamento(valor):
    """Converte "yyyyMMddHHmmss" (formato real confirmado em teste ao vivo,
    não ISO) pra datetime. Retorna None se vier vazio ou num formato
    inesperado — melhor perder a data do que quebrar o insert inteiro."""
    if not valor:
        return None
    try:
        return datetime.strptime(valor[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _extrair_campos(hit: dict) -> dict:
    fonte = hit.get("_source", {})
    classe = fonte.get("classe") or {}
    assuntos = fonte.get("assuntos") or []
    primeiro_assunto = assuntos[0] if assuntos else {}
    orgao = fonte.get("orgaoJulgador") or {}

    return {
        "numero_cnj": fonte.get("numeroProcesso"),
        "classe_codigo": classe.get("codigo"),
        "classe_nome": classe.get("nome"),
        "assunto_codigo": primeiro_assunto.get("codigo"),
        "assunto_nome": primeiro_assunto.get("nome"),
        "data_ajuizamento": _parse_data_ajuizamento(fonte.get("dataAjuizamento")),
        "valor_causa": fonte.get("valorCausa"),
        "orgao_julgador": orgao.get("nome"),
        "grau": fonte.get("grau"),
        "sigiloso": not _nivel_sigilo_eh_publico(fonte),
        "bruto_json": _json_seguro(fonte),
    }


def _gravar_processos(tribunal: str, hits: list[dict]) -> int:
    novos = 0
    uf = uf_do_tribunal(tribunal)
    for hit in hits:
        campos = _extrair_campos(hit)
        if not campos["numero_cnj"]:
            continue
        if campos["sigiloso"]:
            continue
        existente = fetch_one(
            "SELECT 1 AS x FROM processos WHERE numero_cnj = :n", {"n": campos["numero_cnj"]}
        )
        if existente:
            continue
        execute(
            """
            INSERT INTO processos (
                numero_cnj, tribunal, uf, classe_codigo, classe_nome, assunto_codigo,
                assunto_nome, data_ajuizamento, valor_causa, orgao_julgador, grau,
                sigiloso, bruto_json
            ) VALUES (
                :numero_cnj, :tribunal, :uf, :classe_codigo, :classe_nome, :assunto_codigo,
                :assunto_nome, :data_ajuizamento, :valor_causa, :orgao_julgador, :grau,
                :sigiloso, CAST(:bruto_json AS JSONB)
            )
            ON CONFLICT (numero_cnj) DO NOTHING
            """,
            {**campos, "tribunal": tribunal, "uf": uf},
        )

        # Partes às vezes já vêm no próprio DataJud (nem sempre — depende do
        # tribunal). Quando vierem, gravamos com origem 'datajud'; o Módulo 2
        # (raspagem por tribunal) complementa o que faltar.
        for parte in hit.get("_source", {}).get("partes", []) or []:
            polo = (parte.get("polo") or "").upper()
            nome = parte.get("nome")
            if not nome or polo not in ("ATIVO", "PASSIVO"):
                continue
            execute(
                """
                INSERT INTO partes (numero_cnj, polo, nome, cnpj, origem)
                VALUES (:numero_cnj, :polo, :nome, :cnpj, 'datajud')
                ON CONFLICT (numero_cnj, polo, nome) DO NOTHING
                """,
                {
                    "numero_cnj": campos["numero_cnj"],
                    "polo": polo,
                    "nome": nome,
                    "cnpj": (parte.get("cnpj") or "").strip() or None,
                },
            )
        novos += 1
    return novos


def coletar_tribunal(tribunal: str, api_key: str) -> ResultadoColeta:
    resultado = ResultadoColeta(tribunal=tribunal)
    config = get_config("classes_tpu")
    classes_codigos = [c["codigo"] for c in config] if config else []
    assuntos_codigos = [a["codigo"] for a in (get_config("assuntos_tpu") or [])]
    dias_retroativos = get_config("dias_retroativos_busca")

    # "dataAjuizamento" não é um campo "date" do Elasticsearch — é texto no
    # formato compacto "yyyyMMddHHmmss" (confirmado em teste real: um filtro
    # "gte" em formato ISO "yyyy-MM-dd" não filtra NADA, volta o total
    # inteiro do índice; no formato compacto, filtra corretamente).
    data_minima = (datetime.now(timezone.utc) - timedelta(days=dias_retroativos)).strftime("%Y%m%d%H%M%S")
    query = _montar_query(classes_codigos, assuntos_codigos, data_minima)

    search_after = _cursor_salvo(tribunal)

    ritmo = get_config("ritmo_requisicao_segundos")

    while True:
        try:
            pagina = _requisitar_pagina(tribunal, api_key, query, search_after)
        except RuntimeError as exc:
            resultado.erros.append(str(exc))
            break

        hits = pagina.get("hits", {}).get("hits", [])
        if not hits:
            break

        resultado.processos_novos += _gravar_processos(tribunal, hits)
        resultado.paginas_lidas += 1

        search_after = hits[-1].get("sort")
        if search_after:
            _salvar_cursor(tribunal, search_after)

        if len(hits) < TAMANHO_PAGINA:
            break
        time.sleep(ritmo)

    return resultado


def medir_atraso_por_tribunal() -> "query_df":
    """Atraso médio (em dias) entre ajuizamento e descoberta pelo sistema,
    por tribunal — usado no Painel e para o alerta configurável."""
    return query_df(
        """
        SELECT
            tribunal,
            count(*) AS total_processos,
            avg(EXTRACT(EPOCH FROM (data_descoberta - data_ajuizamento)) / 86400.0) AS atraso_medio_dias,
            max(data_descoberta) AS ultima_coleta
        FROM processos
        WHERE data_ajuizamento IS NOT NULL
        GROUP BY tribunal
        ORDER BY tribunal
        """
    )


def executar_coleta_completa() -> list[ResultadoColeta]:
    """Roda a coleta para todos os tribunais ativos configurados. Registra
    cada execução em `execucoes_coleta` pra aparecer no Painel."""
    import os

    api_key = os.environ.get("DATAJUD_API_KEY", "")
    tribunais = get_config("tribunais_ativos") or []
    resultados = []

    for tribunal in tribunais:
        execute(
            "INSERT INTO execucoes_coleta (tipo, tribunal, status) VALUES ('datajud', :t, 'rodando') RETURNING id",
            {"t": tribunal},
        )
        execucao_id = fetch_one(
            "SELECT id FROM execucoes_coleta WHERE tribunal = :t ORDER BY id DESC LIMIT 1",
            {"t": tribunal},
        )["id"]

        try:
            resultado = coletar_tribunal(tribunal, api_key)
            status = "erro" if resultado.erros else "sucesso"
            execute(
                """
                UPDATE execucoes_coleta
                SET status = :status, finalizado_em = now(), processos_novos = :novos,
                    erro_detalhe = :erro
                WHERE id = :id
                """,
                {
                    "status": status,
                    "novos": resultado.processos_novos,
                    "erro": "; ".join(resultado.erros) or None,
                    "id": execucao_id,
                },
            )
            resultados.append(resultado)
        except Exception as exc:  # noqa: BLE001 — queremos registrar qualquer falha e seguir pro próximo tribunal
            execute(
                "UPDATE execucoes_coleta SET status = 'erro', finalizado_em = now(), erro_detalhe = :erro WHERE id = :id",
                {"erro": str(exc), "id": execucao_id},
            )
            resultados.append(ResultadoColeta(tribunal=tribunal, erros=[str(exc)]))

    return resultados
