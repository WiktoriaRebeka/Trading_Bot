# /trading_bot/state_manager.py (WERSJA FINALNA)

import logging
from typing import Iterable, Dict, Any
from datetime import datetime, timezone
from google.cloud.firestore_v1.document import DocumentSnapshot
from google.cloud.firestore_v1.base_client import BaseClient
from google.cloud import firestore

from firebase_client import get_db
import constants

logger = logging.getLogger(__name__)

def _get_db() -> BaseClient:
    return get_db()

# --- ZARZĄDZANIE SETUPAMI ---
def get_all_active_setups() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.SETUP_COLLECTION).stream()

def update_setup_entry_attempt(symbol: str):
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.set({"entry_attempts": firestore.Increment(1)}, merge=True)
    logger.info(f"[{symbol}] Zwiększono licznik prób wejścia.")

def update_setup_after_trade_open(symbol: str):
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.set({"is_position_open_on_this_setup": True}, merge=True)
    logger.info(f"[{symbol}] Zaktualizowano setup: pozycja otwarta.")

def update_setup_after_trade_close(symbol: str, is_loss: bool):
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.set({"is_position_open_on_this_setup": False, "is_reset_needed_after_loss": is_loss}, merge=True)
    logger.info(f"[{symbol}] Zresetowano flagę otwartej pozycji w setupie. is_loss={is_loss}")

def update_setup_after_price_reset(symbol: str):
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.set({"is_reset_needed_after_loss": False}, merge=True)
    logger.info(f"[{symbol}] Warunek resetu ceny spełniony.")

# --- ZARZĄDZANIE OTWARTYMI POZYCJAMI ---
def get_all_open_trades() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.TRADE_COLLECTION).stream()

def create_open_trade(trade_id: str, symbol: str, direction: str, ob_type: str, entry_price: float, sl_price: float, tp_price: float, alert_data: dict):
    db = _get_db()
    trade_doc_ref = db.collection(constants.TRADE_COLLECTION).document(trade_id)
    timestamp_utc = datetime.now(timezone.utc)
    trade_data = {
        "trade_id": trade_id, "symbol": symbol, "direction": direction,
        "ob_type": ob_type, "entry_price": entry_price, "sl_price": sl_price,
        "tp_price": tp_price, "opened_at_ms": int(timestamp_utc.timestamp() * 1000),
        "opened_at_iso": timestamp_utc.isoformat(), "alert_data_snapshot": alert_data
    }
    trade_doc_ref.set(trade_data)
    logger.info(f"[{symbol}][{trade_id}] Utworzono dokument dla otwartej pozycji w '{constants.TRADE_COLLECTION}'.")
    update_setup_after_trade_open(symbol)
    update_setup_entry_attempt(symbol)

def remove_open_trade(trade_id: str):
    _get_db().collection(constants.TRADE_COLLECTION).document(trade_id).delete()
    logger.info(f"[{trade_id}] Usunięto pozycję z aktywnego monitorowania.")

# --- ZARZĄDZANIE ANALIZOWANYMI POZYCJAMI ---
def get_all_analyzed_trades() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.ANALYZED_COLLECTION).stream()

# W pliku /trading_bot/state_manager.py

def create_analyzed_trade(trade_data: Dict):
    """Tworzy dokument "ducha" w kolekcji 'analyzed_trades'."""
    db = _get_db()
    trade_id = trade_data.get('trade_id')
    if not trade_id:
        logger.error("Próba utworzenia 'ducha' bez trade_id!")
        return

    doc_ref = db.collection(constants.ANALYZED_COLLECTION).document(trade_id)
    
    # --- KLUCZOWA ZMIANA: Dodajemy pole 'original_sl' ---
    analysis_data = {
        "trade_id": trade_id,
        "symbol": trade_data.get('symbol'),
        "direction": trade_data.get('direction'),
        "entry_price": trade_data.get('entry_price'),
        "original_sl": trade_data.get('sl_price'), # <-- To pole jest niezbędne!
        "opened_at_ms": trade_data.get('opened_at_ms'),
        "alert_data_snapshot": trade_data.get('alert_data_snapshot', {}),
        "last_bq_update_iso": None # Inicjalizujemy jako puste
    }
    
    doc_ref.set(analysis_data)
    logger.info(f"[{trade_id}] Utworzono pozycję do analizy post-mortem w '{constants.ANALYZED_COLLECTION}'.")
def update_analyzed_trade_timestamp(trade_id: str):
    """Zapisuje znacznik czasu ostatniej udanej aktualizacji BQ."""
    doc_ref = _get_db().collection(constants.ANALYZED_COLLECTION).document(trade_id)
    doc_ref.set({"last_bq_update_iso": datetime.now(timezone.utc).isoformat()}, merge=True)

def remove_analyzed_trade(trade_id: str):
    _get_db().collection(constants.ANALYZED_COLLECTION).document(trade_id).delete()
    logger.info(f"[{trade_id}] Zakończono i usunięto pozycję z analizy post-mortem.")