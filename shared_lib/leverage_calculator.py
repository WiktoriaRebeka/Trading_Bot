# shared_lib/leverage_calculator.py

import logging
from decimal import Decimal, ROUND_DOWN
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

# --- Konfiguracja Strategii Zarządzania Ryzykiem ---
TARGET_RISK_USDT = Decimal("2.50")
BASE_POSITION_VALUE_USDT = Decimal("10.00")


class CalculationResult(NamedTuple):
    """Struktura przechowująca wyniki kalkulacji parametrów zlecenia."""
    final_leverage: int
    final_position_value_usdt: Decimal
    notional_value_usdt: Decimal # Pełna, dźwigniowana wartość pozycji


def calculate_order_parameters(
    entry_price: float, 
    sl_price: float, 
    max_leverage_from_api: float
) -> Optional[CalculationResult]:
    """
    Oblicza finalną dźwignię i wielkość pozycji (margin) zgodnie z logiką
    stałego ryzyka 2.50 USDT.
    """
    try:
        entry_price_d = Decimal(str(entry_price))
        sl_price_d = Decimal(str(sl_price))
        max_leverage_d = Decimal(str(max_leverage_from_api))

        if entry_price_d <= 0:
            logger.error("Cena wejścia jest nieprawidłowa (<= 0).")
            return None

        sl_distance = abs(entry_price_d - sl_price_d)
        if sl_distance == 0:
            logger.error("Odległość Stop Lossa wynosi zero. Nie można obliczyć ryzyka.")
            return None
        sl_distance_percentage = sl_distance / entry_price_d

        loss_on_base_position = BASE_POSITION_VALUE_USDT * sl_distance_percentage
        if loss_on_base_position == 0:
            logger.error("Strata na pozycji bazowej wynosi zero. Nie można obliczyć dźwigni.")
            return None
            
        required_leverage = TARGET_RISK_USDT / loss_on_base_position

        final_position_value_usdt = BASE_POSITION_VALUE_USDT
        final_leverage_decimal = required_leverage

        if required_leverage > max_leverage_d:
            ratio = required_leverage / max_leverage_d
            final_position_value_usdt = BASE_POSITION_VALUE_USDT * ratio
            final_leverage_decimal = max_leverage_d
            logger.warning(
                f"[Kalkulator] Wymagana dźwignia ({required_leverage:.0f}x) > Max giełdy ({max_leverage_d:.0f}x). "
                f"Dostosowuję wielkość pozycji do ~{final_position_value_usdt:.2f} USDT."
            )

        # Oblicz finalną wartość nominalną
        notional_value = final_position_value_usdt * final_leverage_decimal

        return CalculationResult(
            final_leverage=int(final_leverage_decimal),
            final_position_value_usdt=final_position_value_usdt,
            notional_value_usdt=notional_value
        )

    except Exception as e:
        logger.error(f"Nieoczekiwany błąd w kalkulatorze dźwigni: {e}", exc_info=True)
        return None

def format_price(price: float, tick_size: str) -> str:
    """Formatuje cenę zgodnie z tick_size instrumentu."""
    price_decimal = Decimal(str(price))
    tick_size_decimal = Decimal(tick_size)
    formatted_price = price_decimal.quantize(tick_size_decimal, rounding=ROUND_DOWN)
    return str(formatted_price)