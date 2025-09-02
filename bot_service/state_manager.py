# Lokalizacja: bot_service/state_manager.py

import logging
from typing import Optional, Iterable, Dict, Any, List
from datetime import datetime, timezone, timedelta
from google.cloud import firestore
from google.cloud.firestore_v1.document import DocumentSnapshot

from shared_lib.firebase_client import get_db
from shared_lib import constants
from shared_lib.models import AlertData, OpenTradeData

logger = logging.getLogger(__name__)

def _get_db() -> firestore.Client:
    return get_db()

def get_all_active_setups() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.SETUP_COLLECTION).stream()

def update_setup_after_price_reset(symbol: str):
    doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    doc_ref.update({"is_reset_needed_after_loss": False})
    logger.info(f"[{symbol}] Warunek resetu ceny spełniony. Setup gotowy do nowego wejścia.")

def get_all_open_trades() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.TRADE_COLLECTION).stream()

def create_open_trade(trade_id: str, symbol: str, direction: str, ob_type: str, entry_price: float, sl_price: float, tp_price: float, alert_data: AlertData, bybit_order_id: str):
    db = _get_db()
    transaction = db.transaction()
    
    @firestore.transactional
    def _create_trade_in_transaction(transaction, trade_id, symbol, direction, ob_type, entry_price, sl_price, tp_price, alert_data, bybit_order_id):
        trade_doc_ref = db.collection(constants.TRADE_COLLECTION).document(trade_id)
        setup_doc_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)
        
        setup_snapshot = setup_doc_ref.get(transaction=transaction)
        if not setup_snapshot.exists:
            raise RuntimeError(f"Setup dla {symbol} już nie istnieje.")
        if setup_snapshot.to_dict().get("is_position_open_on_this_setup", False):
            raise RuntimeError(f"Pozycja dla setupu {symbol} jest już otwarta.")
            
        timestamp_utc = datetime.now(timezone.utc)
        
        new_trade = OpenTradeData(
            trade_id=trade_id, 
            symbol=symbol, 
            direction=direction.upper(), 
            ob_type=ob_type,
            entry_price=entry_price, 
            sl_price=sl_price, 
            tp_price=tp_price,
            opened_at_ms=int(timestamp_utc.timestamp() * 1000),
            opened_at_iso=timestamp_utc.isoformat(),
            alert_data_snapshot=alert_data.model_dump(by_alias=True),
            bybit_order_id=bybit_order_id  
        )
        
        transaction.set(trade_doc_ref, new_trade.model_dump())
        
        update_data = {
            "is_position_open_on_this_setup": True,
            "entry_attempts": firestore.Increment(1)
        }
        transaction.update(setup_doc_ref, update_data)
        logger.info(f"[{symbol}][{trade_id}] Transakcja przygotowana: utworzenie pozycji (z Bybit ID: {bybit_order_id}) i aktualizacja setupu.")

    try:
        _create_trade_in_transaction(transaction, trade_id, symbol, direction, ob_type, entry_price, sl_price, tp_price, alert_data, bybit_order_id)
        logger.info(f"[{symbol}][{trade_id}] SUKCES. Transakcja atomowa zakończona.")
    except Exception as e:
        logger.error(f"[{symbol}][{trade_id}] BŁĄD TRANSAKCJI: {e}", exc_info=True)
        raise

def get_latest_klines_from_cache(symbols: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    if not symbols: 
        logger.info("[KLINE_CACHE] Otrzymano pustą listę symboli.")
        return {}
    db = _get_db()
    klines_cache = {}
    unique_symbols = list(set(s for s in symbols if isinstance(s, str) and s))
    if not unique_symbols:
        logger.warning("[KLINE_CACHE] Lista symboli po przefiltrowaniu jest pusta.")
        return {}
    logger.info(f"[KLINE_CACHE] Próba pobrania danych dla {len(unique_symbols)} symboli.")
    for i in range(0, len(unique_symbols), 30):
        chunk = unique_symbols[i:i + 30]
        if not chunk: continue
        try:
            docs = db.collection(constants.LATEST_KLINES_COLLECTION).where("__name__", "in", chunk).stream()
            for doc in docs:
                klines_cache[doc.id] = doc.to_dict()
        except Exception as e:
            logger.error(f"[KLINE_CACHE] KRYTYCZNY BŁĄD podczas pobierania danych dla części {chunk}: {e}", exc_info=True)
            continue
    if klines_cache:
        logger.info(f"[KLINE_CACHE] Pomyślnie pobrano łącznie {len(klines_cache)} rekordów kline z cache'u.")
    else:
        logger.warning(f"[KLINE_CACHE] Nie udało się pobrać ŻADNYCH rekordów kline z cache'u.")
    return klines_cache

@firestore.transactional
def update_setup_after_immediate_close_transactional(transaction, symbol: str, is_loss: bool):
    setup_doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
    update_data = {
        "is_position_open_on_this_setup": False,
        "is_reset_needed_after_loss": is_loss,
        "entry_attempts": firestore.Increment(1)
    }
    transaction.update(setup_doc_ref, update_data)
    logger.info(f"[{symbol}] Transakcja przygotowana: aktualizacja setupu po natychmiastowym zamknięciu.")

@firestore.transactional
def close_trade_transactional(transaction, trade_id: str, symbol: str, is_loss: bool):
    db = _get_db()
    trade_doc_ref = db.collection(constants.TRADE_COLLECTION).document(trade_id)
    setup_doc_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)
    transaction.delete(trade_doc_ref)
    update_data = {
        "is_position_open_on_this_setup": False,
        "is_reset_needed_after_loss": is_loss
    }
    transaction.update(setup_doc_ref, update_data)
    logger.info(f"[{trade_id}][{symbol}] Transakcja przygotowana: usunięcie pozycji i reset setupu.")

def get_open_trade_by_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        db = get_db()
        trades_ref = db.collection('open_trades').where('symbol', '==', symbol).limit(1).stream()
        for trade_doc in trades_ref:
            trade_data = trade_doc.to_dict()
            trade_data['trade_id'] = trade_doc.id
            return trade_data
        return None
    except Exception as e:
        logger.error(f"Błąd podczas wyszukiwania otwartego zlecenia dla symbolu {symbol}: {e}")
        return None

def delete_open_trade(trade_id: str):
    try:
        db = get_db()
        db.collection('open_trades').document(trade_id).delete()
        logger.info(f"Pomyślnie usunięto dokument zlecenia {trade_id} z Firestore.")
    except Exception as e:
        logger.error(f"Błąd podczas usuwania dokumentu zlecenia {trade_id}: {e}")

def get_active_setup(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        logger.error(f"Błąd podczas pobierania aktywnego setupu dla {symbol}: {e}")
        return None

def create_setup_from_alert(alert_data: AlertData):
    db = _get_db()
    setup_doc_ref = db.collection(constants.SETUP_COLLECTION).document(alert_data.symbol)
    
    new_setup_data = {
        "alert_data": alert_data.model_dump(by_alias=True),
        "is_position_open_on_this_setup": False,
        "is_reset_needed_after_loss": False,
        "entry_attempts": 0,
        "updated_at": firestore.SERVER_TIMESTAMP
    }
    
    setup_doc_ref.set(new_setup_data)
    logger.info(f"[{alert_data.symbol}] Utworzono/zresetowano setup na podstawie nowego alertu.")

def load_last_pnl_sync_timestamp() -> int:
    """Odczytuje timestamp ostatniej synchronizacji P&L z Firestore."""
    db = get_db()
    doc_ref = db.collection(constants.BOT_CONFIG_COLLECTION).document("pnl_sync_state")
    try:
        doc = doc_ref.get()
        if doc.exists:
            ts = doc.get("last_sync_timestamp_ms")
            if ts:
                logger.info(f"[PNL_SYNC] Odczytano ostatni timestamp synchronizacji P&L: {ts}")
                return ts
    except Exception as e:
        logger.error(f"[PNL_SYNC] Błąd odczytu timestampu P&L: {e}")
    
    fallback_ts = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    logger.warning(f"[PNL_SYNC] Brak timestampu P&L, używam wartości domyślnej: {fallback_ts}")
    return fallback_ts

def save_last_pnl_sync_timestamp(timestamp_ms: int):
    """Zapisuje nowy timestamp ostatniej synchronizacji P&L."""
    db = get_db()
    doc_ref = db.collection(constants.BOT_CONFIG_COLLECTION).document("pnl_sync_state")
    try:
        doc_ref.set({"last_sync_timestamp_ms": timestamp_ms}, merge=True)
        logger.info(f"[PNL_SYNC] Zapisano nowy timestamp synchronizacji P&L: {timestamp_ms}")
    except Exception as e:
        logger.error(f"[PNL_SYNC] Błąd zapisu timestampu P&L: {e}")


def reset_setup_after_trade_close(symbol: str, is_loss: bool):
    """
    Resetuje stan setupu po zamknięciu powiązanej z nim pozycji.
    Kluczowe dla odblokowania możliwości ponownego handlu na danym symbolu.

    Args:
        symbol (str): Symbol, dla którego setup ma być zresetowany.
        is_loss (bool): True, jeśli pozycja zakończyła się stratą.
    """
    try:
        setup_doc_ref = _get_db().collection(constants.SETUP_COLLECTION).document(symbol)
        update_data = {
            "is_position_open_on_this_setup": False,
            "is_reset_needed_after_loss": is_loss
        }
        setup_doc_ref.update(update_data)
        logger.info(f"[{symbol}] SUKCES. Stan setupu został zresetowany po zamknięciu pozycji (is_loss: {is_loss}).")
    except Exception as e:
        logger.error(f"[{symbol}] KRYTYCZNY BŁĄD podczas resetowania stanu setupu: {e}", exc_info=True)