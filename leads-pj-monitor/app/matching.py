"""Módulo 3 (parte 2) — Casamento do polo passivo com o CNPJ.

Ordem de tentativa, por processo:
1. CNPJ já veio no próprio processo (DataJud ou raspagem) e os dígitos
   verificadores conferem -> match direto, sem ambiguidade.
2. Sem CNPJ: razão social normalizada (sem acento/pontuação/sufixo
   LTDA/ME/EPP/EIRELI/S.A.) + UF do processo, comparada por similaridade
   (rapidfuzz) contra `empresas_receita`. Acima do limiar configurado
   (padrão 85) -> match fuzzy, guardando o score. Abaixo -> fila de
   revisão manual.
"""
import re
import unicodedata

import pandas as pd
from rapidfuzz import fuzz, process as rf_process

from app.config import get_config
from app.db import execute, query_df

SUFIXOS_PJ = re.compile(r"\b(LTDA|ME|EPP|EIRELI|S/A|S\.A\.?|SA)\b", re.IGNORECASE)


def normalizar_razao_social(nome: str) -> str:
    nome = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode("ascii")
    nome = nome.upper()
    nome = SUFIXOS_PJ.sub("", nome)
    nome = re.sub(r"[^A-Z0-9 ]", " ", nome)
    nome = re.sub(r"\s+", " ", nome).strip()
    return nome


def _dv_cnpj_valido(cnpj: str) -> bool:
    cnpj = re.sub(r"\D", "", cnpj or "")
    if len(cnpj) != 14 or len(set(cnpj)) == 1:
        return False

    def calcular(base: str, pesos: list[int]) -> int:
        soma = sum(int(d) * p for d, p in zip(base, pesos))
        resto = soma % 11
        return 0 if resto < 2 else 11 - resto

    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    dv1 = calcular(cnpj[:12], pesos1)
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    dv2 = calcular(cnpj[:12] + str(dv1), pesos2)
    return cnpj[12:14] == f"{dv1}{dv2}"


def processar_matching_pendentes() -> dict:
    """Varre partes do polo passivo que ainda não geraram um lead, tenta
    casar com empresas_receita, grava em `leads`. Retorna contadores."""
    limiar = get_config("limiar_similaridade_fuzzy")

    pendentes = query_df(
        """
        SELECT p.numero_cnj, p.nome, p.cnpj, pr.uf AS uf_processo
        FROM partes p
        JOIN processos pr ON pr.numero_cnj = p.numero_cnj
        WHERE p.polo = 'PASSIVO'
          AND NOT EXISTS (SELECT 1 FROM leads l WHERE l.numero_cnj = p.numero_cnj)
        """
    ).fillna("")

    contadores = {"match_exato": 0, "match_fuzzy": 0, "revisao_manual": 0, "sem_match": 0}

    # Cache por UF, não da base inteira: a tabela empresas_receita tem
    # potencialmente dezenas de milhões de linhas (toda a base nacional de
    # CNPJ), carregar isso tudo em memória de uma vez travaria o processo.
    # Como cada tribunal é estadual, filtrar por UF já reduz MUITO o universo.
    cache_por_uf: dict[str, "pd.DataFrame"] = {}

    def _candidatos_da_uf(uf: str | None):
        chave = uf or "_SEM_UF_"
        if chave not in cache_por_uf:
            if uf:
                df = query_df("SELECT cnpj, razao_social FROM empresas_receita WHERE uf = :uf", {"uf": uf})
            else:
                df = query_df("SELECT cnpj, razao_social FROM empresas_receita LIMIT 0")
            df["razao_normalizada"] = df["razao_social"].fillna("").map(normalizar_razao_social)
            cache_por_uf[chave] = df
        return cache_por_uf[chave]

    for _, parte in pendentes.iterrows():
        cnpj_digitos = re.sub(r"\D", "", parte["cnpj"] or "")
        if cnpj_digitos and _dv_cnpj_valido(cnpj_digitos):
            empresa = query_df(
                "SELECT cnpj FROM empresas_receita WHERE cnpj = :c", {"c": cnpj_digitos}
            )
            if not empresa.empty:
                _gravar_lead(parte["numero_cnj"], cnpj_digitos, "cnpj_exato", 100.0)
                contadores["match_exato"] += 1
                continue

        alvo_normalizado = normalizar_razao_social(parte["nome"])
        if not alvo_normalizado:
            contadores["sem_match"] += 1
            continue

        candidatos = _candidatos_da_uf(parte["uf_processo"])
        if candidatos.empty:
            # Sem UF do processo (ou sem empresa nenhuma cadastrada naquela
            # UF): não vale a pena arriscar um fuzzy contra a base nacional
            # inteira em memória — manda pra revisão manual.
            contadores["revisao_manual"] += 1
            continue

        melhor = rf_process.extractOne(
            alvo_normalizado,
            candidatos["razao_normalizada"],
            scorer=fuzz.token_sort_ratio,
        )

        if melhor is None:
            contadores["sem_match"] += 1
            continue

        _, score, idx = melhor
        cnpj_match = candidatos.iloc[idx]["cnpj"]

        if score >= limiar:
            _gravar_lead(parte["numero_cnj"], cnpj_match, "fuzzy", score)
            contadores["match_fuzzy"] += 1
        else:
            _gravar_lead(parte["numero_cnj"], cnpj_match, "fuzzy", score, status="revisao_manual")
            contadores["revisao_manual"] += 1

    return contadores


def _gravar_lead(numero_cnj: str, cnpj: str, metodo: str, score_match: float, status: str = "novo") -> None:
    execute(
        """
        INSERT INTO leads (numero_cnj, cnpj, metodo_match, score_match, status)
        VALUES (:numero_cnj, :cnpj, :metodo, :score, :status)
        ON CONFLICT (numero_cnj, cnpj) DO NOTHING
        """,
        {"numero_cnj": numero_cnj, "cnpj": cnpj, "metodo": metodo, "score": score_match, "status": status},
    )
