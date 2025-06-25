# /trading_bot/state_manager.py (Wersja dla Architektury Dwustanowej v4.1)

import logging
import uuid
from typing import Iterable, Dict, Any, List
from datetime import datetime, timezone
from google.cloud.firestore_v1.document import DocumentSnapshot
from google.cloud.firestore_v1.base_client import BaseClient
from firebase_client import get_db
import constants

logger = logging.getLogger(__name__)

def _get_db() -> BaseClient:
    """Helper do uzyskania instancji DB."""
    return get_db()

# --- Funkcje operujące na kolekcji `active_setups` (kluczem jest symbol) ---

def get_all_setups() -> Iterable[DocumentSnapshot]:
    """Pobiera iterator po wszystkich aktywnych setupach."""
    db = _get_db()
    return db.collection(constants.SETUP_COLLECTION).stream()

def remove_setup(symbol: str):
    """Usuwa dokument setupu z Firestore, unieważniając go."""
    db = _get_db()
    doc_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.delete()
    logger.info(f"[{symbol}] Usunięto aktywny setup ze stanu.")

def update_last_known_price(symbol: str, price: float):
    """Aktualizuje ostatnią znaną cenę dla danego setupu."""
    if price is None:
        return
    db = _get_db()
    doc_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.update({"last_known_price": price})

def increment_entry_attempts(symbol: str):
    """Zwiększa licznik prób wejścia dla danego setupu."""
    from google.cloud import firestore
    db = _get_db()
    doc_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.update({"entry_attempts": firestore.Increment(1)})
    logger.info(f"[{symbol}] Zwiększono licznik prób wejścia.")


# --- Funkcje operujące na kolekcji `active_trades` (kluczem jest trade_id) ---

def get_all_trades() -> Iterable[DocumentSnapshot]:
    """Pobiera iterator po wszystkich otwartych transakcjach."""
    db = _get_db()
    return db.collection(constants.TRADE_COLLECTION).stream()

def get_open_trade_symbols() -> List[str]:
    """Pobiera listę symboli, które mają obecnie otwartą pozycję."""
    trades = get_all_trades()
    return [trade.to_dict().get('symbol') for trade in trades if trade.to_dict().get('symbol')]

def create_trade(symbol: str, alert_data: Dict[str, Any], entry_price: float, ob_type: str):
    """Tworzy nowy dokument dla otwartej transakcji w kolekcji `active_trades`."""
    db = _get_db()
    trade_id = str(uuid.uuid4())
    doc_ref = db.collection(constants.TRADE_COLLECTION).document(trade_id)
    
    timestamp_utc = datetime.now(timezone.utc)

    trade_data = {
        "trade_id": trade_id,
        "symbol": symbol,
        "status": "OPEN", # Pozycja jest od razu otwierana
        "direction": alert_data.get('direction', 'N/A'),
        "entry_price": entry_price,
        "sl": float(alert_data['sl']),
        "tp": float(alert_data['tp']),
        "ob_type": ob_type,
        "entry_timestamp": timestamp_utc.isoformat(),
        "entry_timestamp_ms": int(timestamp_utc.timestamp() * 1000),
        "alert_data": alert_data # Zapisujemy alert dla celów analizy R:R
    }
    
    doc_ref.set(trade_data)
    logger.info(f"[{symbol}] Utworzono nową otwartą pozycję w Firestore o ID: {trade_id}")
    return trade_id

def remove_trade(trade_id: str):
    """Usuwa dokument transakcji z kolekcji stanu po jej zakończeniu."""
    db = _get_db()
    doc_ref = db.collection(constants.TRADE_COLLECTION).document(trade_id)
    doc_ref.delete()
    logger.info(f"[{trade_id}] Usunięto zakończoną transakcję z `active_trades`.")
