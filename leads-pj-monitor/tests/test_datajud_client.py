"""Testes da extração de campos do retorno do DataJud (app/datajud_client.py).
Usa um payload sintético no formato esperado do Elasticsearch — não bate na
API real (ver nota no topo de app/datajud_client.py sobre o que foi validado
ao vivo e o que não foi)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.datajud_client import _extrair_campos, _montar_query, _nivel_sigilo_eh_publico


def _hit_exemplo(**sobrescritas):
    fonte = {
        "numeroProcesso": "00012345620268260100",
        "classe": {"codigo": 1116, "nome": "Execução Fiscal"},
        "assuntos": [{"codigo": 7771, "nome": "Dívida Ativa"}],
        "dataAjuizamento": "2026-01-15T00:00:00Z",
        "valorCausa": 15000.50,
        "orgaoJulgador": {"nome": "1ª Vara de Execuções Fiscais"},
        "grau": "G1",
        "nivelSigilo": 0,
    }
    fonte.update(sobrescritas)
    return {"_source": fonte, "sort": [fonte["dataAjuizamento"], "id-exemplo"]}


def test_extrair_campos_processo_publico():
    campos = _extrair_campos(_hit_exemplo())
    assert campos["numero_cnj"] == "00012345620268260100"
    assert campos["classe_codigo"] == 1116
    assert campos["classe_nome"] == "Execução Fiscal"
    assert campos["assunto_codigo"] == 7771
    assert campos["valor_causa"] == 15000.50
    assert campos["sigiloso"] is False


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
