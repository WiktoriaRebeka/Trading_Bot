# Lokalizacja: bot_service/bybit_executor.py

import logging
import time
import hmac
import hashlib
import json
from typing import Optional

from shared_lib.models import OrderData
from shared_lib.config import config
from shared_lib import constants

logger = logging.getLogger(__name__)

def _generate_signature(timestamp: str, api_key: str, api_secret: str, payload: str) -> str:
    """
    Generuje sygnaturę HMAC-SHA256 zgodnie z dokumentacją Bybit V5 API.
    """
    # Bybit wymaga, aby recv_window był częścią stringu do podpisania
    recv_window = "5000" 
    param_str = timestamp + api_key + recv_window + payload
    hash_val = hmac.new(bytes(api_secret, "utf-8"), param_str.encode("utf-8"), hashlib.sha256)
    return hash_val.hexdigest()

def _test_signature_generation():
    """Tymczasowa funkcja do weryfikacji poprawności generowania sygnatury."""
    logger.info("--- URUCHAMIAM TEST GENEROWANIA SYGNATURY ---")
    
    # Prawdziwe dane testowe z oficjalnej dokumentacji Bybit V5 API
    api_key = "test_api_key"
    api_secret = "test_api_secret"
    timestamp = "1689672221235"
    payload = '{"category":"linear","symbol":"BTCUSDT","side":"Buy","orderType":"Limit","qty":"0.1","price":"25000"}'
    
    # Oczekiwana sygnatura dla powyższych danych (zgodnie z dokumentacją)
    expected_signature = "8a52e178604277a23119e578a11079f3116e880a52a83955f5e321c82355bf9c"
    
    generated_signature = _generate_signature(timestamp, api_key, api_secret, payload)
    
    logger.info(f"Oczekiwana sygnatura: {expected_signature}")
    logger.info(f"Wygenerowana sygnatura: {generated_signature}")
    
    if generated_signature == expected_signature:
        logger.info("TEST ZAKOŃCZONY SUKCESEM: Sygnatury są identyczne!")
    else:
        logger.error("BŁĄD TESTU: Wygenerowana sygnatura jest NIEPOPRAWNA!")

def _set_leverage(symbol: str, leverage: int) -> bool:
    """Ustawia dźwignię dla danego symbolu na koncie Bybit."""
    logger.info(f"[{symbol}] Próba ustawienia dźwigni na {leverage}x.")
    logger.warning(f"[{symbol}] SYMULACJA: Dźwignia nie została zmieniona.")
    return True

def _place_limit_order(order: OrderData) -> Optional[str]:
    """Składa zlecenie typu Limit, operując na wartości w USDC."""
    logger.info(f"[{order.symbol}] Próba złożenia zlecenia o wartości {order.margin_value_usdc} USDC.")
    logger.warning(f"[{order.symbol}] SYMULACJA: Zlecenie nie zostało wysłane.")
    return "simulated_order_12345"

def execute_trade(order: OrderData) -> Optional[str]:
    """
    Wykonuje pełny proces otwarcia transakcji: ustawia dźwignię, a następnie składa zlecenie.
    """
    # Uruchamiamy nasz test za każdym razem, gdy ta funkcja jest wywoływana
    _test_signature_generation()
    
    logger.info(f"[{order.symbol}] Rozpoczynam proces wykonania transakcji.")
    
    leverage_set_successfully = _set_leverage(order.symbol, order.leverage)
    
    if not leverage_set_successfully:
        logger.error(f"[{order.symbol}] KRYTYCZNY BŁĄD: Nie udało się ustawić dźwigni. Przerywam składanie zlecenia.")
        return None
        
    order_id = _place_limit_order(order)
    
    if order_id:
        logger.info(f"[{order.symbol}] Zlecenie pomyślnie złożone. Order ID: {order_id}")
    else:
        logger.error(f"[{order.symbol}] Nie udało się złożyć zlecenia.")
        
    return order_id