# /trading_bot/state_manager.py (WERSJA ZMODYFIKOWANA)

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
        'last_known_price': None,
        # Usuwamy ewentualne stare dane pozycji, jeśli setup jest nadpisywany
        'position_data': None 
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
    if symbol in active_setups and active_setups[symbol].get('is_new'):
        active_setups[symbol]['is_new'] = False
        logger.info(f"[{symbol}] Zmieniono status setupu na: Old OB.")


def set_position_status(symbol: str, is_open: bool, trade_details: Optional[Dict] = None):
    """
    Ustawia flagę pozycji i przechowuje szczegóły transakcji przy jej otwarciu.
    """
    if symbol in active_setups:
        active_setups[symbol]['is_position_open'] = is_open
        
        if is_open and trade_details:
            # Gdy otwieramy pozycję, zapisujemy cały słownik szczegółów
            active_setups[symbol]['position_data'] = trade_details
            logger.info(f"[{symbol}] Zmieniono status pozycji na: OTWARTA. Zapisano dane transakcji ID: {trade_details.get('trade_id')}")
        
        elif not is_open:
            # Gdy zamykamy, czyścimy dane pozycji
            if 'position_data' in active_setups[symbol]:
                del active_setups[symbol]['position_data']
            logger.info(f"[{symbol}] Zmieniono status pozycji na: ZAMKNIĘTA.")
    else:
        logger.warning(f"[{symbol}] Próba zmiany statusu pozycji dla nieistniejącego setupu.")


def update_last_known_price(symbol: str, price: Optional[float]):
    """Aktualizuje ostatnią znaną cenę dla symbolu."""
    if symbol in active_setups:
        active_setups[symbol]['last_known_price'] = price


# === NOWE FUNKCJE DO ZARZĄDZANIA STANEM OTWARTEJ POZYCJI ===

def update_max_profit_price(symbol: str, new_max_profit_price: float):
    """Aktualizuje cenę maksymalnego osiągniętego profitu w stanie pozycji."""
    if symbol in active_setups and active_setups[symbol].get('position_data'):
        active_setups[symbol]['position_data']['max_profit_price'] = new_max_profit_price
    else:
        logger.warning(f"[{symbol}] Próba aktualizacji max_profit_price dla nieistniejącej lub zamkniętej pozycji.")


def update_rr_flags(symbol: str, updated_flags: Dict[str, bool]):
    """Aktualizuje słownik z flagami osiągniętych poziomów RR."""
    if symbol in active_setups and active_setups[symbol].get('position_data'):
        active_setups[symbol]['position_data']['rr_achieved_flags'] = updated_flags
    else:
        logger.warning(f"[{symbol}] Próba aktualizacji flag RR dla nieistniejącej lub zamkniętej pozycji.")