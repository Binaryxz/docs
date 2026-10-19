"""Testes do raspador de partes do e-SAJ/TJSP (app/tjsp_scraper.py). Não
tocam a rede — só a parte pura (parse do número CNJ, normalização de
polo, limpeza do HTML da parte)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tjsp_scraper import _limpa_nome, _normaliza_papel, _parse_numero_cnj


def test_parse_numero_cnj_formato_real():
    r = _parse_numero_cnj("10004711620268260607")
    assert r == {"seq": "1000471", "dv": "16", "ano": "2026", "foro": "0607"}


def test_parse_numero_cnj_tamanho_invalido_retorna_none():
    assert _parse_numero_cnj("123") is None
    assert _parse_numero_cnj("") is None


def test_normaliza_papel_variacoes_de_genero():
    # Achado real: processo com ré mulher vem como "Exectda", não "Exectdo"
    # — o primeiro mapeamento só tinha a forma masculina.
    assert _normaliza_papel("Exectdo") == "PASSIVO"
    assert _normaliza_papel("Exectda") == "PASSIVO"
    assert _normaliza_papel("Exeqte") == "ATIVO"
    assert _normaliza_papel("Requerida") == "PASSIVO"


def test_normaliza_papel_desconhecido_retorna_none():
    assert _normaliza_papel("Terceiro Interessado") is None


def test_limpa_nome_corta_antes_do_advogado():
    html = """

            PREFEITURA MUNICIPAL DE TABAPUÃ

        <br />
        <span class="mensagemExibindo">Advogada:&nbsp;</span>
        Aline Marini Tardivo Valderrama
    """
    assert _limpa_nome(html) == "PREFEITURA MUNICIPAL DE TABAPUÃ"


def test_limpa_nome_sem_advogado():
    html = "\n\n    Carlos Roberto Martins\n\n"
    assert _limpa_nome(html) == "Carlos Roberto Martins"
