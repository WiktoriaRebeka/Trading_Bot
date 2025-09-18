# shared_lib/risk_manager.py

import logging
from decimal import Decimal, ROUND_DOWN
from typing import Optional

logger = logging.getLogger(__name__)

# --- Stałe Konfiguracyjne Ryzyka ---
FEE_MAKER = 0.00020  # 0.02%
FEE_TAKER = 0.00055  # 0.055%
TOTAL_FEE_PERCENT = FEE_MAKER + FEE_TAKER # 0.075%

def round_quantity_by_step(quantity: float, qty_step: str) -> float:
    """
    Zaokrągla ilość (Qty) w dół do najbliższego dozwolonego kroku (step).
    Używamy zaokrąglania w dół, aby nigdy nie przekroczyć naszego ryzyka.
    """
    try:
        quantity_decimal = Decimal(str(quantity))
        step_decimal = Decimal(qty_step)
        
        # Dzieli ilość przez krok, zaokrągla w dół do liczby całkowitej, a następnie mnoży z powrotem.
        quantized_qty = (quantity_decimal / step_decimal).to_integral_value(rounding=ROUND_DOWN) * step_decimal
        
        return float(quantized_qty)
    except Exception as e:
        logger.error(f"Błąd podczas zaokrąglania ilości: {e}", exc_info=True)
        return 0.0


def calculate_position_size(
    risk_per_trade_usdt: float,
    entry_price: float,
    sl_price: float,
    qty_step: str
) -> Optional[float]:
    """
    Oblicza finalną, zaokrągloną ilość (Qty) kryptowaluty na podstawie
    zdefiniowanego ryzyka w USDT i nominalnej odległości do SL.
    Zakładamy, że opłaty są już uwzględnione w poziomach TP przesyłanych w alercie.
    """
    if entry_price <= 0 or sl_price <= 0:
        logger.warning("Cena wejścia i SL muszą być dodatnie.")
        return None

    # 1. Oblicz nominalną odległość do SL w punktach (dolarach na jednostkę)
    risk_per_unit = abs(entry_price - sl_price)

    if risk_per_unit == 0:
        logger.warning("Odległość do SL wynosi zero, nie można obliczyć wielkości pozycji.")
        return None

    # 2. Oblicz idealną ilość (Qty) na podstawie zdefiniowanego ryzyka w USDT
    # Wzór: Ryzyko [USDT] / Ryzyko na jednostkę [USDT/jednostkę] = Ilość [jednostek]
    ideal_qty = risk_per_trade_usdt / risk_per_unit

    # 3. Zaokrąglij ilość w dół do najbliższego dozwolonego kroku
    final_qty = round_quantity_by_step(ideal_qty, qty_step)

    logger.info(
        f"Obliczanie wielkości pozycji (uproszczone): Ryzyko={risk_per_trade_usdt} USDT, "
        f"Entry={entry_price}, SL={sl_price}, "
        f"Ryzyko na jednostkę={risk_per_unit:.4f} USDT, "
        f"Finalna ilość (Qty)={final_qty}"
    )

    return final_qty