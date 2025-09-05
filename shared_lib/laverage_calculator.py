import logging
from typing import Dict, Optional
import math

from shared_lib.models import AlertData

logger = logging.getLogger(__name__)

# --- STAŁE KONFIGURACYJNE ---
RISK_PER_TRADE_PERCENT = 2.5
POSITION_SIZE_PERCENT = 10.0
TOTAL_CAPITAL = 100.0
TRANSACTION_FEE_PERCENT = 0.02

def calculate_sl_distance_points(entry_price: float, sl_price: float) -> float:
    """Oblicza bezwzględną odległość w punktach między ceną wejścia a stop lossem."""
    if entry_price == sl_price:
        logger.warning(f"Cena wejścia ({entry_price}) i stop loss ({sl_price}) są identyczne.")
        return 0.0
    distance = abs(entry_price - sl_price)
    return round(distance, 8)

def calculate_sl_distance_percentage(entry_price: float, sl_price: float) -> float:
    """Oblicza odległość stop lossa jako procent ceny wejścia."""
    if entry_price == 0:
        logger.error("Cena wejścia wynosi 0. Nie można obliczyć procentowej odległości SL.")
        return 0.0
    sl_distance_points = calculate_sl_distance_points(entry_price, sl_price)
    if sl_distance_points == 0.0:
        return 0.0
    percentage = (sl_distance_points / entry_price) * 100
    return round(percentage, 4)

def calculate_real_sl_distance_percentage(sl_distance_percentage: float) -> float:
    """Oblicza realną procentową odległość SL, uwzględniając prowizje transakcyjne."""
    total_fee = TRANSACTION_FEE_PERCENT * 2
    real_distance = sl_distance_percentage + total_fee
    return round(real_distance, 4)

def calculate_required_leverage(real_sl_percentage: float) -> Optional[int]:
    """
    Oblicza wymaganą dźwignię, zaokrąglając ją w dół do najbliższej liczby całkowitej.
    """
    if real_sl_percentage <= 0:
        logger.error(
            f"Realna odległość SL ({real_sl_percentage}%) jest zerowa lub ujemna. "
            "Nie można obliczyć dźwigni."
        )
        return None
    
    risk_in_usd = (RISK_PER_TRADE_PERCENT / 100) * TOTAL_CAPITAL
    margin_in_usd = (POSITION_SIZE_PERCENT / 100) * TOTAL_CAPITAL
    loss_on_margin_in_usd = (real_sl_percentage / 100) * margin_in_usd

    if loss_on_margin_in_usd <= 0:
        logger.error("Strata na marginie jest zerowa lub ujemna. Nie można obliczyć dźwigni.")
        return None

    leverage = risk_in_usd / loss_on_margin_in_usd

    # --- KLUCZOWA ZMIANA: ZAOKRĄGLANIE W DÓŁ ---
    # Używamy math.floor do obcięcia części dziesiętnej i rzutujemy na int.
    safe_leverage = math.floor(leverage)

    # Dodatkowe zabezpieczenie: dźwignia nie może być mniejsza niż 1.
    if safe_leverage < 1:
        logger.warning(
            f"Obliczona dźwignia ({leverage:.2f}x) jest mniejsza niż 1. "
            "Oznacza to, że ryzyko jest bardzo duże. Zwracam None, aby uniknąć transakcji."
        )
        return None

    return int(safe_leverage)

def get_all_calculations_for_alert(alert: AlertData) -> Dict[str, Optional[float | int]]:
    """
    Kompleksowa funkcja, która dla danego alertu oblicza wszystkie parametry ryzyka i dźwigni.
    """
    entry = alert.entry
    sl = alert.sl
    
    distance_points = calculate_sl_distance_points(entry, sl)
    distance_percentage = calculate_sl_distance_percentage(entry, sl)
    real_distance_percentage = calculate_real_sl_distance_percentage(distance_percentage)
    required_leverage = calculate_required_leverage(real_distance_percentage)

    return {
        "distance_points": distance_points,
        "distance_percentage": distance_percentage,
        "distance_percentage_real": real_distance_percentage,
        "required_leverage": required_leverage
    }