"""Testes do Módulo 4 — validação de WhatsApp (app/whatsapp_validation.py).
Não tocam no banco nem na API — só a parte pura (normalização de número)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.whatsapp_validation import normaliza_e164


def test_normaliza_numero_sem_ddi_adiciona_55():
    assert normaliza_e164("11987654321") == "+5511987654321"
    assert normaliza_e164("1126721010") == "+551126721010"


def test_normaliza_numero_ja_com_ddi_nao_duplica():
    assert normaliza_e164("5511987654321") == "+5511987654321"


def test_normaliza_remove_formatacao():
    assert normaliza_e164("(11) 98765-4321") == "+5511987654321"


def test_normaliza_string_vazia():
    assert normaliza_e164("") == ""
    assert normaliza_e164(None) == ""
