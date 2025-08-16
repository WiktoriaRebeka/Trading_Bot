# Lokalizacja: shared_lib/leverage_calculator.py

import logging
from typing import Dict, Optional
import math
from decimal import Decimal, ROUND_DOWN
from shared_lib.models import AlertData

logger = logging.getLogger(__name__)

def format_price(price: Decimal, tick_size: str) -> str: # Zmieniono typ z float na Decimal
    # Funkcja już używa Decimal, więc jest gotowa na tę zmianę
    price_decimal = Decimal(str(price))
    tick_size_decimal = Decimal(tick_size)
    formatted_price = price_decimal.quantize(tick_size_decimal, rounding=ROUND_DOWN)
    return str(formatted_price)

# === ZMIANA STAŁYCH NA TYP DECIMAL DLA BEZPIECZEŃSTWA OBLICZEŃ ===
RISK_PER_TRADE_PERCENT = Decimal("2.5")
POSITION_SIZE_PERCENT = Decimal("10.0")
TOTAL_CAPITAL = Decimal("100.0")
TRANSACTION_FEE_PERCENT = Decimal("0.02")

def calculate_sl_distance_points(entry_price: Decimal, sl_price: Decimal) -> Decimal:
    if entry_price == sl_price:
        logger.warning(f"Cena wejścia ({entry_price}) i stop loss ({sl_price}) są identyczne.")
        return Decimal("0.0")
    distance = abs(entry_price - sl_price)
    return distance # Nie ma potrzeby zaokrąglać, Decimal zachowuje precyzję

def calculate_sl_distance_percentage(entry_price: Decimal, sl_price: Decimal) -> Decimal:
    if entry_price == Decimal("0"):
        logger.error("Cena wejścia wynosi 0. Nie można obliczyć procentowej odległości SL.")
        return Decimal("0.0")
    sl_distance_points = calculate_sl_distance_points(entry_price, sl_price)
    if sl_distance_points == Decimal("0.0"):
        return Decimal("0.0")
    percentage = (sl_distance_points / entry_price) * Decimal("100")
    return percentage

def calculate_real_sl_distance_percentage(sl_distance_percentage: Decimal) -> Decimal:
    total_fee = TRANSACTION_FEE_PERCENT * Decimal("2")
    real_distance = sl_distance_percentage + total_fee
    return real_distance

def calculate_required_leverage(real_sl_percentage: Decimal) -> Optional[int]:
    if real_sl_percentage <= Decimal("0"):
        logger.error(f"Realna odległość SL ({real_sl_percentage}%) jest zerowa lub ujemna. Nie można obliczyć dźwigni.")
        return None

    risk_in_usd = (RISK_PER_TRADE_PERCENT / Decimal("100")) * TOTAL_CAPITAL
    margin_in_usd = (POSITION_SIZE_PERCENT / Decimal("100")) * TOTAL_CAPITAL
    loss_on_margin_in_usd = (real_sl_percentage / Decimal("100")) * margin_in_usd
    
    if loss_on_margin_in_usd <= Decimal("0"):
        logger.error("Strata na marginie jest zerowa lub ujemna. Nie można obliczyć dźwigni.")
        return None

    leverage = risk_in_usd / loss_on_margin_in_usd
    safe_leverage = math.floor(leverage)
    
    if safe_leverage < 1:
        logger.warning(f"Obliczona dźwignia ({leverage:.2f}x) jest mniejsza niż 1. Zwracam None.")
        return None

    return int(safe_leverage)

def get_all_calculations_for_alert(alert: AlertData) -> Dict[str, Optional[Decimal | int]]:
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