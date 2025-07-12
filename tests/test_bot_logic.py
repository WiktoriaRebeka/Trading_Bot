# Lokalizacja: /tests/test_bot_logic.py
import pytest
from bot_service.bot_logic import _calculate_rr_analytics

def test_calculate_rr_long_win():
    """Testuje poprawny zysk dla pozycji LONG."""
    result = _calculate_rr_analytics(entry_price=100, sl_price=98, extreme_price=104, direction="LONG")
    assert result["rr_achieved"] == 2.0
    assert result["rr_2_0_achieved"] is True
    assert result["rr_3_0_achieved"] is False

def test_calculate_rr_short_win():
    """Testuje poprawny zysk dla pozycji SHORT."""
    result = _calculate_rr_analytics(entry_price=100, sl_price=105, extreme_price=90, direction="SHORT")
    assert result["rr_achieved"] == 2.0
    assert result["rr_2_0_achieved"] is True
    assert result["rr_3_0_achieved"] is False

def test_calculate_rr_long_loss_direction():
    """Testuje brak zysku, gdy cena idzie w złą stronę dla LONG."""
    result = _calculate_rr_analytics(entry_price=100, sl_price=98, extreme_price=99, direction="LONG")
    assert result["rr_achieved"] == 0.0
    assert result["rr_1_0_achieved"] is False

def test_calculate_rr_short_loss_direction():
    """Testuje brak zysku, gdy cena idzie w złą stronę dla SHORT."""
    result = _calculate_rr_analytics(entry_price=100, sl_price=102, extreme_price=101, direction="SHORT")
    assert result["rr_achieved"] == 0.0
    assert result["rr_1_0_achieved"] is False

def test_calculate_rr_zero_risk():
    """Testuje przypadek, gdy ryzyko wynosi zero."""
    result = _calculate_rr_analytics(entry_price=100, sl_price=100, extreme_price=110, direction="LONG")
    assert result["rr_achieved"] == 0.0