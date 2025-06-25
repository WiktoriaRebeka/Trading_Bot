# /trading_bot/state_manager.py (WERSJA FINALNA - Architektura Dwustanowa)

import logging
from typing import Iterable, Optional, Dict, Any
from datetime import datetime
from google.cloud.firestore_v1.document import DocumentSnapshot
from google.cloud.firestore_v1.base_client import BaseClient
from google.cloud import firestore

from firebase_client import get_db
import constants

logger = logging.getLogger(__name__)

def _get_db() -> BaseClient:
    """Helper do uzyskania instancji DB."""
    return get_db()

# --- Funkcje dla Setupów (Kolekcja `active_setups`) ---

def get_all_active_setups() -> Iterable[DocumentSnapshot]:
    """Pobiera wszystkie aktywne setupy (jeden na symbol)."""
    db = _get_db()
    return db.collection(constants.SETUP_COLLECTION).stream()

def get_active_setup(symbol: str) -> Optional[Dict[str, Any]]:
    """Odczytuje aktywny setup dla danego symbolu."""
    db = _get_db()
    doc_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    return None

def update_setup_entry_attempt(symbol: str):
    """Inkrementuje licznik prób wejścia dla danego setupu."""
    db = _get_db()
    doc_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.update({"entry_attempts": firestore.Increment(1)})

# --- Funkcje dla Otwartych Transakcji (Kolekcja `open_trades`) ---

def get_all_open_trades() -> Iterable[DocumentSnapshot]:
    """Pobiera wszystkie otwarte transakcje do monitorowania."""
    db = _get_db()
    return db.collection(constants.TRADE_COLLECTION).stream()

def is_position_open_for_symbol(symbol: str) -> bool:
    """Sprawdza, czy istnieje jakakolwiek otwarta pozycja dla danego symbolu."""
    db = _get_db()
    trades = db.collection(constants.TRADE_COLLECTION).where("symbol", "==", symbol).limit(1).get()
    return len(trades) > 0

def create_open_trade(trade_id: str, symbol: str, direction: str, ob_type: str, entry_price: float, sl_price: float, tp_price: float, alert_data: dict):
    """Tworzy nowy, odizolowany dokument dla otwartej transakcji."""
    db = _get_db()
    trade_doc_ref = db.collection(constants.TRADE_COLLECTION).document(trade_id)
    
    timestamp_utc = datetime.now(timezone.utc)
    
    trade_data = {
        "trade_id": trade_id,
        "symbol": symbol,
        "direction": direction,
        "ob_type": ob_type,
        "entry_price": entry_price,
        "sl_price": sl_price,  # "Zamrożony" SL z momentu wejścia
        "tp_price": tp_price,  # "Zamrożony" TP z momentu wejścia
        "opened_at_ms": int(timestamp_utc.timestamp() * 1000),
        "opened_at_iso": timestamp_utc.isoformat(),
        "alert_data_snapshot": alert_data # Zapisujemy kopię alertu dla celów analitycznych
    }
    trade_doc_ref.set(trade_data)
    logger.info(f"[{symbol}][{trade_id}] Zapisano otwartą pozycję do '{constants.TRADE_COLLECTION}'.")
    
    # Jednocześnie aktualizujemy licznik w setupie
    update_setup_entry_attempt(symbol)

def remove_closed_trade(trade_id: str):
    """Usuwa dokument zamkniętej transakcji z kolekcji monitorowania."""
    db = _get_db()
    doc_ref = db.collection(constants.TRADE_COLLECTION).document(trade_id)
    doc_ref.delete()
    logger.info(f"[{trade_id}] Usunięto zamkniętą pozycję ze stanu monitorowania.")