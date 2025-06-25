# /trading_bot/state_manager.py (Wersja dla Architektury Stanu Ciągłego v5.1)

import logging
from typing import Iterable, Optional, Dict, Any
from datetime import datetime
from google.cloud.firestore_v1.document import DocumentSnapshot
from google.cloud.firestore_v1.base_client import BaseClient
from google.cloud import firestore  # Import dla firestore.Increment

from firebase_client import get_db
import constants

logger = logging.getLogger(__name__)

def _get_db() -> BaseClient:
    """Helper do uzyskania instancji DB."""
    return get_db()

def get_active_setup(symbol: str) -> Optional[Dict[str, Any]]:
    """Odczytuje aktywny setup dla danego symbolu."""
    db = _get_db()
    doc_ref = db.collection(constants.STATE_COLLECTION).document(symbol)
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    return None

def get_all_active_symbols() -> Iterable[DocumentSnapshot]:
    """Pobiera iterator po wszystkich dokumentach (aktywnych setupach)."""
    db = _get_db()
    return db.collection(constants.STATE_COLLECTION).stream()

def update_last_known_price(symbol: str, price: float):
    """Aktualizuje ostatnią znaną cenę dla danego setupu."""
    if price is None:
        return
    db = _get_db()
    doc_ref = db.collection(constants.STATE_COLLECTION).document(symbol)
    doc_ref.update({"last_known_price": price})

def open_new_position(symbol: str, trade_id: str, entry_price: float, timestamp_utc: datetime):
    """
    Ustawia pozycję jako otwartą, zapisuje jej szczegóły i inkrementuje licznik prób wejścia.
    """
    db = _get_db()
    doc_ref = db.collection(constants.STATE_COLLECTION).document(symbol)
    
    update_data = {
        "is_position_open": True,
        "active_trade_id": trade_id,
        "active_trade_entry_price": entry_price,
        "active_trade_entry_timestamp_ms": int(timestamp_utc.timestamp() * 1000),
        "entry_attempts": firestore.Increment(1)  # Atomowa inkrementacja licznika
    }
    doc_ref.update(update_data)
    logger.info(f"[{symbol}] Zaktualizowano stan na OTWARTY. ID transakcji: {trade_id}. Licznik prób zwiększony.")

def close_active_position(symbol: str):
    """
    Resetuje stan aktywnej pozycji (ustawia is_position_open na False),
    ale zachowuje setup, pozwalając na kolejne wejścia (Used OB).
    """
    db = _get_db()
    doc_ref = db.collection(constants.STATE_COLLECTION).document(symbol)
    update_data = {
        "is_position_open": False,
        "active_trade_id": None,
        "active_trade_entry_price": None,
        "active_trade_entry_timestamp_ms": None
    }
    doc_ref.update(update_data)
    logger.info(f"[{symbol}] Zresetowano stan aktywnej pozycji. Setup gotowy na ponowne wejście (Used OB).")

def remove_setup(symbol: str):
    """
    Całkowicie usuwa setup dla danego symbolu z Firestore, np. po unieważnieniu
    przez SL/TP lub nowy alert (co dzieje się w main.py przez .set()).
    """
    db = _get_db()
    doc_ref = db.collection(constants.STATE_COLLECTION).document(symbol)
    doc_ref.delete()
    logger.info(f"[{symbol}] Usunięto cały aktywny setup ze stanu.")
