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
    Oblicza finalną, zaokrągloną ilość (Qty), uwzględniając bufor na poślizg
    oraz twardy limit maksymalnego ryzyka.
    """
    if entry_price <= 0 or sl_price <= 0:
        logger.warning("Cena wejścia i SL muszą być dodatnie.")
        return None

    # --- POCZĄTEK ZABEZPIECZENIA: TWARDY LIMIT RYZYKA ---
    MAX_RISK_PER_TRADE_USDT = 2.5
    if risk_per_trade_usdt > MAX_RISK_PER_TRADE_USDT:
        logger.warning(
            f"KRYTYCZNE ZABEZPIECZENIE: Próba ustawienia ryzyka ({risk_per_trade_usdt} USDT) powyżej maksymalnego limitu. "
            f"Ryzyko zostało przymusowo ograniczone do {MAX_RISK_PER_TRADE_USDT} USDT."
        )
        risk_per_trade_usdt = MAX_RISK_PER_TRADE_USDT
    # --- KONIEC ZABEZPIECZENIA ---

    # 1. Oblicz nominalne ryzyko z ruchu ceny w procentach
    nominal_risk_perc = abs(entry_price - sl_price) / entry_price
    if nominal_risk_perc == 0:
        logger.warning("Dystans między ceną wejścia a SL wynosi zero. Nie można obliczyć wielkości pozycji.")
        return None
    
    # 2. Dodaj bufor bezpieczeństwa na poślizg cenowy (slippage)
    SLIPPAGE_BUFFER_PERCENT = 0.0005  # 0.05%
    total_risk_perc = nominal_risk_perc + SLIPPAGE_BUFFER_PERCENT
    
    # 3. Oblicz docelową wartość pozycji w USDT
    position_value_usdt = risk_per_trade_usdt / total_risk_perc
    
    # 4. Przelicz wartość w USDT na idealną ilość kryptowaluty
    ideal_qty = position_value_usdt / entry_price
    
    # 5. Zaokrąglij ilość w dół do najbliższego dozwolonego kroku
    final_qty = round_quantity_by_step(ideal_qty, qty_step)
    
    logger.info(
        f"Obliczanie wielkości pozycji: Ryzyko={risk_per_trade_usdt} USDT, "
        f"Entry={entry_price}, SL={sl_price}, "
        f"Całkowite ryzyko % (cena+poślizg)={total_risk_perc:.4f}, "
        f"Wartość pozycji={position_value_usdt:.2f} USDT, "
        f"Finalna ilość (Qty)={final_qty}"
    )
    
    return final_qty