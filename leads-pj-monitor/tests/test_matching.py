"""Testes do casamento de nome/CNPJ (app/matching.py). Não tocam no banco —
só a parte pura (normalização e validação de dígito verificador)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.matching import _dv_cnpj_valido, normalizar_razao_social


def test_normalizar_remove_sufixo_pj():
    assert normalizar_razao_social("Padaria Bom Pao LTDA") == "PADARIA BOM PAO"
    assert normalizar_razao_social("Comercial XYZ S.A.") == "COMERCIAL XYZ"
    assert normalizar_razao_social("Oficina do Zé EIRELI") == "OFICINA DO ZE"


def test_normalizar_remove_acentos_e_pontuacao():
    assert normalizar_razao_social("Açaí & Cia Ltda.") == "ACAI CIA"


def test_normalizar_string_vazia():
    assert normalizar_razao_social("") == ""
    assert normalizar_razao_social(None) == ""


def test_dv_cnpj_valido_aceita_cnpj_real():
    # CNPJ real da Receita Federal (órgão público, dado conhecido/estável).
    assert _dv_cnpj_valido("00000000000191") is True


def test_dv_cnpj_valido_rejeita_digito_verificador_errado():
    assert _dv_cnpj_valido("00000000000199") is False


def test_dv_cnpj_valido_rejeita_tamanho_errado():
    assert _dv_cnpj_valido("123") is False
    assert _dv_cnpj_valido("") is False


def test_dv_cnpj_valido_rejeita_todos_digitos_iguais():
    assert _dv_cnpj_valido("11111111111111") is False
