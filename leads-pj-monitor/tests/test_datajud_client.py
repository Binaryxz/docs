"""Testes da extração de campos do retorno do DataJud (app/datajud_client.py).
Payload sintético, mas no formato REAL confirmado contra a API ao vivo com
a chave pública do CNJ (ver nota no topo de app/datajud_client.py) — inclui
o detalhe de "dataAjuizamento" vir como string compacta "yyyyMMddHHmmss",
não ISO, que é exatamente o que essas datas no exemplo abaixo reproduzem."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.datajud_client import (
    _extrair_campos,
    _montar_query,
    _nivel_sigilo_eh_publico,
    _parse_data_ajuizamento,
)


def _hit_exemplo(**sobrescritas):
    fonte = {
        "numeroProcesso": "00012345620268260100",
        "classe": {"codigo": 1116, "nome": "Execução Fiscal"},
        "assuntos": [{"codigo": 7771, "nome": "Dívida Ativa"}],
        "dataAjuizamento": "20260115083000",
        "valorCausa": 15000.50,
        "orgaoJulgador": {"nome": "1ª Vara de Execuções Fiscais"},
        "grau": "G1",
        "nivelSigilo": 0,
    }
    fonte.update(sobrescritas)
    return {"_source": fonte, "sort": [fonte["dataAjuizamento"], "id-exemplo"]}


def test_parse_data_ajuizamento_formato_real():
    assert _parse_data_ajuizamento("20260730012658") == datetime(2026, 7, 30, 1, 26, 58)


def test_parse_data_ajuizamento_vazio_ou_invalido_retorna_none():
    assert _parse_data_ajuizamento(None) is None
    assert _parse_data_ajuizamento("") is None
    assert _parse_data_ajuizamento("não é uma data") is None


def test_extrair_campos_processo_publico():
    campos = _extrair_campos(_hit_exemplo())
    assert campos["numero_cnj"] == "00012345620268260100"
    assert campos["classe_codigo"] == 1116
    assert campos["classe_nome"] == "Execução Fiscal"
    assert campos["assunto_codigo"] == 7771
    assert campos["valor_causa"] == 15000.50
    assert campos["sigiloso"] is False
    assert campos["data_ajuizamento"] == datetime(2026, 1, 15, 8, 30, 0)


def test_extrair_campos_sem_nivel_sigilo_trata_como_publico():
    hit = _hit_exemplo()
    del hit["_source"]["nivelSigilo"]
    campos = _extrair_campos(hit)
    assert campos["sigiloso"] is False


def test_extrair_campos_processo_sigiloso():
    campos = _extrair_campos(_hit_exemplo(nivelSigilo=5))
    assert campos["sigiloso"] is True


def test_extrair_campos_sem_classe_nao_quebra():
    hit = _hit_exemplo()
    del hit["_source"]["classe"]
    campos = _extrair_campos(hit)
    assert campos["classe_codigo"] is None
    assert campos["numero_cnj"] == "00012345620268260100"


def test_nivel_sigilo_eh_publico():
    assert _nivel_sigilo_eh_publico({"nivelSigilo": 0}) is True
    assert _nivel_sigilo_eh_publico({}) is True
    assert _nivel_sigilo_eh_publico({"nivelSigilo": 1}) is False


def test_montar_query_com_classes_e_assuntos():
    query = _montar_query([1116, 156], [7771], "2026-01-01")
    assert {"terms": {"classe.codigo": [1116, 156]}} in query["bool"]["must"]
    assert {"terms": {"assuntos.codigo": [7771]}} in query["bool"]["must"]
    assert query["bool"]["filter"] == [{"range": {"dataAjuizamento": {"gte": "2026-01-01"}}}]


def test_montar_query_sem_classes_nem_assuntos():
    query = _montar_query([], [], "2026-01-01")
    assert "must" not in query["bool"]
