# Lokalizacja: tests/test_leverage_calculator.py

import pytest
from shared_lib.leverage_calculator import (
    calculate_sl_distance_points,
    calculate_sl_distance_percentage
)

# Testy dla odległości w punktach
def test_sl_distance_points_long_position():
    assert calculate_sl_distance_points(entry_price=100.0, sl_price=98.0) == pytest.approx(2.0)

def test_sl_distance_points_short_position():
    assert calculate_sl_distance_points(entry_price=500.0, sl_price=505.0) == pytest.approx(5.0)

def test_sl_distance_points_zero_distance():
    assert calculate_sl_distance_points(entry_price=200.0, sl_price=200.0) == 0.0

# Testy dla odległości w procentach
def test_sl_distance_percentage_simple_case():
    # 2 punkty odległości od 100 to 2%
    assert calculate_sl_distance_percentage(entry_price=100.0, sl_price=98.0) == pytest.approx(2.0)

def test_sl_distance_percentage_short_case():
    # 10 punktów odległości od 200 to 5%
    assert calculate_sl_distance_percentage(entry_price=200.0, sl_price=210.0) == pytest.approx(5.0)

def test_sl_distance_percentage_zero_entry_price():
    # Powinno bezpiecznie zwrócić 0 i nie rzucać błędu
    assert calculate_sl_distance_percentage(entry_price=0.0, sl_price=10.0) == 0.0

def test_sl_distance_percentage_zero_distance():
    assert calculate_sl_distance_percentage(entry_price=150.0, sl_price=150.0) == 0.0