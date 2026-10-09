"""Módulo 2 (primeiro tribunal) — raspagem de partes no e-SAJ do TJSP.

Por que isso existe: confirmado em testes reais (ver app/datajud_client.py)
que o DataJud não traz o campo "partes" pra boa parte dos processos do TJSP
(pelo menos nas classes monitoradas — Execução Fiscal, Cumprimento de
Sentença, Execução de Título Extrajudicial). Sem nome da parte não tem o
que casar com CNPJ, então pelo menos um tribunal precisa de raspagem
própria. TJSP foi o escolhido por ser o maior volume e já ter o padrão de
URL/HTML validado manualmente numa rodada anterior.

Fonte: cpopg (Consulta de Processos de 1º Grau) do e-SAJ, busca por número
unificado do processo (CNJ). A página de resultado (quando o processo é
único) já vem com a tabela de partes — não precisa de uma segunda
requisição pra abrir o processo.

Mapeamento de polo: "Exeqte"/"Exequente"/"Credor"/"Reqte"/"Autor"/"Apte" ->
ATIVO; "Exectdo"/"Executado"/"Devedor"/"Reqdo"/"Réu"/"Apdo" -> PASSIVO
(só o PASSIVO interessa pro matching — é quem a empresa ré/executada é).
Papel não reconhecido não é gravado com polo (fica de fora do matching em
vez de arriscar classificar errado).
"""
import re
import time

import requests

from app.db import execute, query_df

BASE_URL = "https://esaj.tjsp.jus.br/cpopg/search.do"

# Inclui variação de gênero (masc/fem) — achado real rodando em lote: um
# processo com ré mulher vem como "Exectda", não "Exectdo", e meu primeiro
# mapeamento só tinha a forma masculina (teria descartado a parte).
PAPEIS_ATIVO = {
    "exeqte", "exequente", "credor", "credora", "reqte", "requerente",
    "autor", "autora", "apte", "apelante",
}
PAPEIS_PASSIVO = {
    "exectdo", "exectda", "executado", "executada", "devedor", "devedora",
    "reqdo", "reqda", "requerido", "requerida", "reu", "ré", "re",
    "apdo", "apda", "apelado", "apelada",
}

_PADRAO_PARTE = re.compile(
    r'tipoDeParticipacao">([^<]*?)&nbsp;</span>.*?class="nomeParteEAdvogado"[^>]*>(.*?)</td>',
    re.DOTALL,
)


def _parse_numero_cnj(numero_cnj: str) -> dict | None:
    """CNJ: NNNNNNN-DD.AAAA.J.TR.OOOO (20 dígitos sem formatação aqui)."""
    digitos = re.sub(r"\D", "", numero_cnj or "")
    if len(digitos) != 20:
        return None
    return {
        "seq": digitos[0:7],
        "dv": digitos[7:9],
        "ano": digitos[9:13],
        "foro": digitos[16:20],
    }


def _normaliza_papel(papel_bruto: str) -> str | None:
    chave = papel_bruto.strip().lower()
    if chave in PAPEIS_ATIVO:
        return "ATIVO"
    if chave in PAPEIS_PASSIVO:
        return "PASSIVO"
    return None


def _limpa_nome(td_html: str) -> str:
    """O td de parte também contém o nome do advogado depois de "Advogad[ao]:"
    — corta ali e pega só o primeiro trecho de texto não vazio."""
    trecho_parte = re.split(r"Advogad[ao]", td_html, maxsplit=1)[0]
    texto = re.sub(r"<[^>]+>", " ", trecho_parte)
    texto = texto.replace("&nbsp;", " ")
    for linha in texto.splitlines():
        limpa = linha.strip()
        if limpa:
            return limpa
    return ""


def buscar_partes(numero_cnj: str, timeout: int = 30) -> list[dict]:
    """Consulta o e-SAJ pra um processo e devolve [{"nome": ..., "polo": ...}].
    Lista vazia se o processo não foi encontrado, está em segredo de justiça,
    ou a página não trouxe tabela de partes reconhecível."""
    partes_cnj = _parse_numero_cnj(numero_cnj)
    if not partes_cnj:
        return []

    params = {
        "conversationId": "",
        "cbPesquisa": "NUMPROC",
        "numeroDigitoAnoUnificado": f"{partes_cnj['seq']}-{partes_cnj['dv']}.{partes_cnj['ano']}",
        "foroNumeroUnificado": partes_cnj["foro"],
        "dadosConsulta.valorConsultaNuUnificado": re.sub(r"\D", "", numero_cnj),
        "dadosConsulta.tipoNuProcesso": "UNIFICADO",
    }
    resp = requests.get(BASE_URL, params=params, timeout=timeout)
    resp.raise_for_status()

    partes = []
    for papel_bruto, td_html in _PADRAO_PARTE.findall(resp.text):
        polo = _normaliza_papel(papel_bruto)
        nome = _limpa_nome(td_html)
        if nome and polo:
            partes.append({"nome": nome, "polo": polo})
    return partes


def _gravar_partes(numero_cnj: str, partes: list[dict]) -> None:
    for parte in partes:
        execute(
            """
            INSERT INTO partes (numero_cnj, nome, polo, cnpj, origem)
            VALUES (:n, :nome, :polo, NULL, 'tjsp_esaj')
            ON CONFLICT DO NOTHING
            """,
            {"n": numero_cnj, "nome": parte["nome"], "polo": parte["polo"]},
        )


def raspar_tjsp_pendentes(ritmo_segundos: float = 2.0, limite: int | None = None) -> dict:
    """Varre processos do TJSP que ainda não têm nenhuma parte gravada e
    busca no e-SAJ. Ritmo conservador por padrão (2s entre requisições) —
    é infraestrutura pública de um tribunal, não uma API dedicada."""
    pendentes = query_df(
        """
        SELECT pr.numero_cnj
        FROM processos pr
        WHERE pr.tribunal = 'api_publica_tjsp'
          AND NOT EXISTS (SELECT 1 FROM partes p WHERE p.numero_cnj = pr.numero_cnj)
        ORDER BY pr.numero_cnj
        """
    )
    if limite:
        pendentes = pendentes.head(limite)

    resultado = {"processados": 0, "com_partes": 0, "sem_partes": 0, "erros": []}
    for _, row in pendentes.iterrows():
        numero_cnj = row["numero_cnj"]
        try:
            partes = buscar_partes(numero_cnj)
        except requests.RequestException as exc:
            resultado["erros"].append(f"{numero_cnj}: {exc}")
            time.sleep(ritmo_segundos)
            continue

        if partes:
            _gravar_partes(numero_cnj, partes)
            resultado["com_partes"] += 1
        else:
            resultado["sem_partes"] += 1
        resultado["processados"] += 1
        time.sleep(ritmo_segundos)

    return resultado
