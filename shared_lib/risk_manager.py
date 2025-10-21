# shared_lib/risk_manager.py


import logging
from decimal import Decimal, ROUND_DOWN
from typing import Optional

logger = logging.getLogger(__name__)

def round_quantity_by_step(quantity: float, qty_step: str) -> float:
    """
    Zaokrągla ilość (Qty) w dół do najbliższego dozwolonego kroku (step).
    """
    try:
        quantity_decimal = Decimal(str(quantity))
        step_decimal = Decimal(qty_step)
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
    Oblicza finalną, zaokrągloną ilość (Qty).
    Nie uwzględnia już opłat, ponieważ PnL z Bybit jest wartością netto.
    """
    if entry_price <= 0 or sl_price <= 0:
        logger.warning("Cena wejścia i SL muszą być dodatnie.")
        return None

    # 1. Oblicz nominalne ryzyko z samego ruchu ceny
    risk_distance = abs(entry_price - sl_price)
    if risk_distance == 0:
        logger.warning("Dystans między ceną wejścia a SL wynosi zero. Nie można obliczyć wielkości pozycji.")
        return None

    # 2. Oblicz idealną ilość kryptowaluty na podstawie ryzyka
    # Ilość = Ryzyko_w_USD / Ryzyko_na_jednostkę_w_USD
    ideal_qty = risk_per_trade_usdt / risk_distance
    
    # 3. Zaokrąglij ilość w dół do najbliższego dozwolonego kroku
    final_qty = round_quantity_by_step(ideal_qty, qty_step)
    
    logger.info(
        f"Obliczanie wielkości pozycji: Ryzyko={risk_per_trade_usdt} USDT, "
        f"Entry={entry_price}, SL={sl_price}, Dystans={risk_distance}, "
        f"Idealna ilość={ideal_qty}, Finalna ilość (Qty)={final_qty}"
    )
    
    return final_qty