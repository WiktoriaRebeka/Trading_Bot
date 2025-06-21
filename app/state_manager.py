# TRADING_BOT/app/state_manager.py 

import logging
from typing import Dict, Optional, Any, List

logger = logging.getLogger(__name__)

# Globalny słownik przechowujący stan aktywnych setupów dla każdego symbolu
active_setups: Dict[str, Dict[str, Any]] = {}

def set_active_setup(symbol: str, ob_data: dict):
    """
    Ustawia nowy Order Block jako aktywny setup dla danego symbolu.
    Nadpisuje stary setup i resetuje wszystkie stany.
    """
    active_setups[symbol] = {
        'ob_data': ob_data,
        'is_new': True,
        'is_position_open': False,
        'last_known_price': None  # Resetujemy ostatnią znaną cenę
    }
    logger.info(f"[{symbol}] Ustawiono nowy, aktywny setup Order Block. Status: New OB.")

def get_active_setup(symbol: str) -> Optional[Dict[str, Any]]:
    """Zwraca aktywny setup dla danego symbolu."""
    return active_setups.get(symbol)

def get_all_active_symbols() -> List[str]:
    """Zwraca listę wszystkich symboli, które mają aktywny setup."""
    return list(active_setups.keys())

def mark_setup_as_old(symbol: str):
    """Zmienia status Order Blocka z 'New OB' na 'Old OB'."""
    if symbol in active_setups and active_setups[symbol]['is_new']:
        active_setups[symbol]['is_new'] = False
        logger.info(f"[{symbol}] Zmieniono status setupu na: Old OB.")

def set_position_status(symbol: str, is_open: bool):
    """Ustawia flagę informującą, czy pozycja dla danego setupu jest otwarta."""
    if symbol in active_setups:
        active_setups[symbol]['is_position_open'] = is_open
        status_str = "OTWARTA" if is_open else "ZAMKNIĘTA"
        logger.info(f"[{symbol}] Zmieniono status pozycji na: {status_str}.")

def update_last_known_price(symbol: str, price: float):
    """Aktualizuje ostatnią znaną cenę dla symbolu."""
    if symbol in active_setups:
        active_setups[symbol]['last_known_price'] = price