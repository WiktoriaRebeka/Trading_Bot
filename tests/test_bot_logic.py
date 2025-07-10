# Lokalizacja: tests/test_bot_logic.py

import pytest
from bot_service.bot_logic import _calculate_rr_analytics

# Testy dla kluczowej funkcji obliczającej R:R
# Używamy dekoratora 'parametrize', aby przetestować wiele przypadków za jednym razem.
@pytest.mark.parametrize("entry, sl, extreme, expected_rr, expected_tp2_flag", [
    # Przypadek 1: Standardowy LONG, osiągnięte R:R 2.0
    (100, 90, 120, 2.0, True),
    # Przypadek 2: Standardowy LONG, nieosiągnięte nawet R:R 1.0
    (100, 90, 105, 0.5, False),
    # Przypadek 3: Standardowy SHORT, osiągnięte R:R 3.0
    (50, 60, 20, 3.0, True),
    # Przypadek 4: Standardowy SHORT, osiągnięte R:R 1.5
    (50, 60, 35, 1.5, False),
    # Przypadek 5: Wejście i SL w tym samym miejscu (ryzyko 0)
    (100, 100, 110, 0.0, False),
    # Przypadek 6: Cena poszła w przeciwnym kierunku (ujemny profit)
    (100, 90, 80, 0.0, False), # Profit nie może być ujemny, więc R:R = 0
])
def test_calculate_rr_analytics(entry, sl, extreme, expected_rr, expected_tp2_flag):
    """
    Testuje funkcję _calculate_rr_analytics dla różnych scenariuszy.
    """
    # ARRANGE (niepotrzebne, dane są w parametrach)
    
    # ACT - Wywołujemy testowaną funkcję
    analytics = _calculate_rr_analytics(entry, sl, extreme)
    
    # ASSERT - Sprawdzamy, czy wyniki są zgodne z oczekiwaniami
    assert analytics["rr_achieved"] == expected_rr
    assert analytics.get("rr_2_0_achieved", False) is expected_tp2_flag
    
    # Sprawdzamy, czy flaga TP1 jest poprawnie ustawiana
    if expected_rr >= 1.0:
        assert analytics["rr_1_0_achieved"] is True
    else:
        assert analytics.get("rr_1_0_achieved", False) is False