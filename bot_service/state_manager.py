#trading_bot/bot_service/state_manager.py

import logging
from typing import Iterable, Dict, Any
from datetime import datetime, timezone
from google.cloud import firestore
from google.cloud.firestore_v1.document import DocumentSnapshot

from firebase_client import get_db
import constants
from models import AlertData, OpenTradeData, AnalyzedTradeData

# Używamy __name__, aby logger automatycznie przyjął nazwę modułu: 'state_manager'
logger = logging.getLogger(__name__)

# Typowanie klienta dla lepszej czytelności i autouzupełniania
def _get_db() -> firestore.Client:
    return get_db()

# --- ZARZĄDZANIE SETUPAMI ---

def get_all_active_setups() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.SETUP_COLLECTION).stream()

def update_setup_entry_attempt(symbol: str):
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.update({"entry_attempts": firestore.Increment(1)})
    logger.debug(f"[{symbol}] Zwiększono licznik prób wejścia.")

def update_setup_after_trade_open(symbol: str):
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.update({"is_position_open_on_this_setup": True})
    logger.info(f"[{symbol}] Zaktualizowano setup: pozycja otwarta.")

def update_setup_after_trade_close(symbol: str, is_loss: bool):
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.update({"is_position_open_on_this_setup": False, "is_reset_needed_after_loss": is_loss})
    logger.info(f"[{symbol}] Zresetowano flagę otwartej pozycji w setupie. is_loss={is_loss}")

def update_setup_after_price_reset(symbol: str):
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.update({"is_reset_needed_after_loss": False})
    logger.info(f"[{symbol}] Warunek resetu ceny spełniony. Setup gotowy do nowego wejścia.")

# --- ZARZĄDZANIE OTWARTYMI POZYCJAMI ---

def get_all_open_trades() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.TRADE_COLLECTION).stream()

def create_open_trade(trade_id: str, symbol: str, direction: str, ob_type: str, entry_price: float, sl_price: float, tp_price: float, alert_data: AlertData):
    db = _get_db()
    trade_doc_ref = db.collection(constants.TRADE_COLLECTION).document(trade_id)
    timestamp_utc = datetime.now(timezone.utc)
    
    new_trade = OpenTradeData(
        trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
        entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
        opened_at_ms=int(timestamp_utc.timestamp() * 1000),
        opened_at_iso=timestamp_utc.isoformat(),
        alert_data_snapshot=alert_data.dict(by_alias=True)
    )
    
    trade_doc_ref.set(new_trade.dict())
    logger.info(f"[{symbol}][{trade_id}] Utworzono dokument dla otwartej pozycji w '{constants.TRADE_COLLECTION}'.")
    update_setup_after_trade_open(symbol)
    update_setup_entry_attempt(symbol)

def remove_open_trade(trade_id: str):
    _get_db().collection(constants.TRADE_COLLECTION).document(trade_id).delete()
    logger.info(f"[{trade_id}] Usunięto pozycję z aktywnego monitorowania.")

# --- ZARZĄDZANIE ANALIZOWANYMI POZYCJAMI ---

def get_all_analyzed_trades() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.ANALYZED_COLLECTION).stream()

def create_analyzed_trade(trade_data: OpenTradeData):
    db = _get_db()
    trade_id = trade_data.trade_id
    if not trade_id: return

    doc_ref = db.collection(constants.ANALYZED_COLLECTION).document(trade_id)
    timestamp_utc = datetime.now(timezone.utc)

    analysis_data = AnalyzedTradeData(
        trade_id=trade_id, symbol=trade_data.symbol, direction=trade_data.direction,
        entry_price=trade_data.entry_price, original_sl=trade_data.sl_price,
        opened_at_ms=trade_data.opened_at_ms,
        alert_data_snapshot=trade_data.alert_data_snapshot,
        last_analysis_timestamp_ms=int(timestamp_utc.timestamp() * 1000),
        last_known_extreme_price=trade_data.entry_price,
        last_bq_update_iso=None
    )
    doc_ref.set(analysis_data.dict())
    logger.info(f"[{trade_id}] Utworzono pozycję do analizy post-mortem w '{constants.ANALYZED_COLLECTION}'.")

def update_analyzed_trade_timestamp(trade_id: str):
    doc_ref = _get_db().collection(constants.ANALYZED_COLLECTION).document(trade_id)
    doc_ref.update({"last_bq_update_iso": datetime.now(timezone.utc)})
    logger.debug(f"[{trade_id}] Zaktualizowano znacznik czasu 'last_bq_update_iso' w Firestore.")

def update_analyzed_trade_analysis_timestamp(trade_id: str, timestamp_ms: int):
    doc_ref = _get_db().collection(constants.ANALYZED_COLLECTION).document(trade_id)
    doc_ref.update({"last_analysis_timestamp_ms": timestamp_ms})

def update_analyzed_trade_analysis_state(trade_id: str, timestamp_ms: int, new_extreme_price: float):
    doc_ref = _get_db().collection(constants.ANALYZED_COLLECTION).document(trade_id)
    update_data = {
        "last_analysis_timestamp_ms": timestamp_ms,
        "last_known_extreme_price": new_extreme_price
    }
    doc_ref.update(update_data)
    logger.info(f"[{trade_id}] Zaktualizowano stan 'ducha' z nową ceną ekstremalną: {new_extreme_price}")

def remove_analyzed_trade(trade_id: str):
    _get_db().collection(constants.ANALYZED_COLLECTION).document(trade_id).delete()
    logger.info(f"[{trade_id}] Zakończono i usunięto pozycję z analizy post-mortem.")

def get_latest_klines_from_cache(symbols: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    if not symbols: return {}
    db = _get_db()
    klines_cache = {}
    unique_symbols = list(set(symbols))
    for i in range(0, len(unique_symbols), 30):
        chunk = unique_symbols[i:i + 30]
        try:
            docs = db.collection(constants.LATEST_KLINES_COLLECTION).where(firestore.FieldPath.document_id(), "in", chunk).stream()
            for doc in docs:
                klines_cache[doc.id] = doc.to_dict()
        except Exception as e:
            logger.error(f"Błąd podczas pobierania danych kline z cache'u dla chunk'a: {chunk}. Błąd: {e}")
    if klines_cache:
        logger.info(f"Pobrano {len(klines_cache)} rekordów kline z cache'u w Firestore.")
    return klines_cache