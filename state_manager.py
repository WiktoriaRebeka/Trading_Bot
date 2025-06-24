# /trading_bot/state_manager.py (Wersja Finalna - Przebudowa na Trade-Centric)

import logging
from typing import Iterable
from datetime import datetime
from google.cloud.firestore_v1.document import DocumentSnapshot
from firebase_client import get_db
from google.cloud.firestore_v1.base_client import BaseClient
import constants

logger = logging.getLogger(__name__)

def _get_db() -> BaseClient:
    """Helper do uzyskania instancji DB."""
    return get_db()

def get_all_active_trades() -> Iterable[DocumentSnapshot]:
    """Pobiera iterator po wszystkich dokumentach z kolekcji stanu."""
    db = _get_db()
    return db.collection(constants.STATE_COLLECTION).stream()

def update_trade_status_to_open(trade_id: str, entry_price: float, entry_timestamp_utc: datetime):
    """Aktualizuje status transakcji na 'OPEN' i zapisuje dane wejścia."""
    db = _get_db()
    doc_ref = db.collection(constants.STATE_COLLECTION).document(trade_id)
    update_data = {
        "status": "OPEN",
        "entry_price": entry_price,
        "entry_timestamp": entry_timestamp_utc.isoformat(),
        "entry_timestamp_ms": int(entry_timestamp_utc.timestamp() * 1000)
    }
    doc_ref.update(update_data)
    logger.info(f"[{trade_id}] Zmieniono status na OPEN w Firestore.")

def update_last_known_price(trade_id: str, price: float):
    """Aktualizuje ostatnią znaną cenę dla danego setupu."""
    db = _get_db()
    if price is None:
        return
    doc_ref = db.collection(constants.STATE_COLLECTION).document(trade_id)
    doc_ref.update({"last_known_price": price})

def remove_trade(trade_id: str):
    """Usuwa dokument transakcji z kolekcji stanu po jej zakończeniu."""
    db = _get_db()
    doc_ref = db.collection(constants.STATE_COLLECTION).document(trade_id)
    doc_ref.delete()
    logger.info(f"[{trade_id}] Usunięto zakończoną transakcję ze stanu w Firestore.")