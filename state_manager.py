# /trading_bot/state_manager.py (WERSJA FINALNA v5.2 - Architektura Wielo-Kolekcyjna)

import logging
from typing import Iterable, Optional, Dict, Any
from datetime import datetime, timezone
from google.cloud.firestore_v1.document import DocumentSnapshot
from google.cloud.firestore_v1.base_client import BaseClient
from google.cloud import firestore

from firebase_client import get_db
import constants

logger = logging.getLogger(__name__)

def _get_db() -> BaseClient:
    """Helper do uzyskania instancji DB."""
    return get_db()

# ==============================================================================
# === ZARZĄDZANIE SETUPAMI (KOLEKCJA `active_setups`) ===
# ==============================================================================

def get_all_active_setups() -> Iterable[DocumentSnapshot]:
    """Pobiera wszystkie aktywne setupy (jeden na symbol)."""
    return _get_db().collection(constants.SETUP_COLLECTION).stream()

def update_setup_after_trade_open(symbol: str):
    """Aktualizuje setup po otwarciu nowej pozycji."""
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    update_data = {
        "is_position_open_on_this_setup": True,
        "entry_attempts": firestore.Increment(1)
    }
    doc_ref.update(update_data)
    logger.info(f"[{symbol}] Zaktualizowano setup: pozycja otwarta, zwiększono licznik prób.")

def update_setup_after_trade_close(symbol: str, is_loss: bool):
    """Aktualizuje setup po zamknięciu pozycji."""
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    update_data = {
        "is_position_open_on_this_setup": False,
        "is_reset_needed_after_loss": is_loss
    }
    # Używamy .set(merge=True), aby uniknąć błędu, jeśli setup został w międzyczasie nadpisany
    doc_ref.set(update_data, merge=True)
    logger.info(f"[{symbol}] Zresetowano flagę otwartej pozycji w setupie. is_loss={is_loss}")

def update_setup_after_price_reset(symbol: str):
    """Oznacza, że warunek resetu ceny po przegranej został spełniony."""
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.update({"is_reset_needed_after_loss": False})
    logger.info(f"[{symbol}] Warunek resetu ceny spełniony. Można ponownie wchodzić w pozycję.")

# ==============================================================================
# === ZARZĄDZANIE OTWARTYMI POZYCJAMI (KOLEKCJA `open_trades`) ===
# ==============================================================================

def get_all_open_trades() -> Iterable[DocumentSnapshot]:
    """Pobiera wszystkie aktywnie monitorowane, otwarte pozycje."""
    return _get_db().collection(constants.TRADE_COLLECTION).stream()

def create_open_trade(trade_id: str, symbol: str, direction: str, ob_type: str, entry_price: float, sl_price: float, tp_price: float, alert_data: dict):
    """Tworzy nowy, odizolowany dokument dla otwartej transakcji."""
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

def remove_open_trade(trade_id: str):
    """Usuwa dokument zamkniętej transakcji z kolekcji `open_trades`."""
    db = _get_db()
    doc_ref = db.collection(constants.TRADE_COLLECTION).document(trade_id)
    doc_ref.delete()
    logger.info(f"[{trade_id}] Usunięto pozycję z aktywnego monitorowania.")

# ==============================================================================
# === ZARZĄDZANIE ANALIZOWANYMI POZYCJAMI (KOLEKCJA `analyzed_trades`) ===
# ==============================================================================

def get_all_analyzed_trades() -> Iterable[DocumentSnapshot]:
    """Pobiera wszystkie pozycje w stanie pasywnej analizy post-mortem."""
    return _get_db().collection(constants.ANALYZED_COLLECTION).stream()

def create_analyzed_trade(trade_data: Dict):
    """Tworzy dokument dla pozycji do dalszej analizy po jej zamknięciu."""
    db = _get_db()
    trade_id = trade_data['trade_id']
    doc_ref = db.collection(constants.ANALYZED_COLLECTION).document(trade_id)
    
    analysis_data = {
        "trade_id": trade_id, "symbol": trade_data['symbol'],
        "direction": trade_data['direction'], "original_sl": trade_data['sl_price'],
        "alert_data_snapshot": trade_data.get('alert_data_snapshot', {})
    }
    doc_ref.set(analysis_data)
    logger.info(f"[{trade_id}] Utworzono pozycję do analizy post-mortem.")

def remove_analyzed_trade(trade_id: str):
    """Usuwa pozycję po zakończeniu jej analizy (osiągnięcie SL lub TP5)."""
    db = _get_db()
    doc_ref = db.collection(constants.ANALYZED_COLLECTION).document(trade_id)
    doc_ref.delete()
    logger.info(f"[{trade_id}] Zakończono i usunięto pozycję z analizy post-mortem.")