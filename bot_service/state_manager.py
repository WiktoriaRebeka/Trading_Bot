# Lokalizacja: bot_service/state_manager.py

import logging
from typing import Iterable, Dict, Any
from datetime import datetime, timezone
from google.cloud import firestore
from google.cloud.firestore_v1.document import DocumentSnapshot

from shared_lib.firebase_client import get_db
from shared_lib import constants
from shared_lib.models import AlertData, OpenTradeData, AnalyzedTradeData

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

def create_open_trade(trade_id: str, symbol: str, direction: str, ob_type: str, entry_price: float, sl_price: float, tp_price: float, alert_data: AlertData):
    db = _get_db()
    transaction = db.transaction()
    
    @firestore.transactional
    def _create_trade_in_transaction(transaction, trade_id, symbol, direction, ob_type, entry_price, sl_price, tp_price, alert_data):
        trade_doc_ref = db.collection(constants.TRADE_COLLECTION).document(trade_id)
        setup_doc_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)
        setup_snapshot = setup_doc_ref.get(transaction=transaction)
        if not setup_snapshot.exists:
            raise RuntimeError(f"Setup dla {symbol} już nie istnieje.")
        if setup_snapshot.to_dict().get("is_position_open_on_this_setup", False):
            raise RuntimeError(f"Pozycja dla setupu {symbol} jest już otwarta.")
        timestamp_utc = datetime.now(timezone.utc)
        new_trade = OpenTradeData(
            trade_id=trade_id, symbol=symbol, direction=direction.upper(), ob_type=ob_type,
            entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
            opened_at_ms=int(timestamp_utc.timestamp() * 1000),
            opened_at_iso=timestamp_utc.isoformat(),
            alert_data_snapshot=alert_data.model_dump(by_alias=True)
        )
        transaction.set(trade_doc_ref, new_trade.model_dump())
        update_data = {
            "is_position_open_on_this_setup": True,
            "entry_attempts": firestore.Increment(1)
        }
        transaction.update(setup_doc_ref, update_data)
        logger.info(f"[{symbol}][{trade_id}] Transakcja przygotowana: utworzenie pozycji i aktualizacja setupu.")

    try:
        _create_trade_in_transaction(transaction, trade_id, symbol, direction, ob_type, entry_price, sl_price, tp_price, alert_data)
        logger.info(f"[{symbol}][{trade_id}] SUKCES. Transakcja atomowa zakończona.")
    except Exception as e:
        logger.error(f"[{symbol}][{trade_id}] BŁĄD TRANSAKCJI: {e}", exc_info=True)
        raise

def get_all_analyzed_trades() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.ANALYZED_COLLECTION).stream()

def create_analyzed_trade(trade_data: OpenTradeData):
    db = _get_db()
    trade_id = trade_data.trade_id
    if not trade_id:
        logger.error("[CREATE_GHOST] Brak trade_id.")
        return
    logger.info(f"[CREATE_GHOST][{trade_id}] Tworzenie 'ducha' dla transakcji WIN.")
    try:
        doc_ref = db.collection(constants.ANALYZED_COLLECTION).document(trade_id)
        tp5_value = trade_data.alert_data_snapshot.get('tp_5_0')
        analysis_data = AnalyzedTradeData(
            trade_id=trade_id,
            symbol=trade_data.symbol,
            direction=trade_data.direction,
            entry_price=trade_data.entry_price,
            original_sl=trade_data.sl_price,
            original_tp_5_0=float(tp5_value) if tp5_value is not None else None,
            opened_at_ms=trade_data.opened_at_ms,
            alert_data_snapshot=trade_data.alert_data_snapshot,
            last_known_extreme_price=trade_data.tp_price,
            last_analysis_timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            achieved_tps=["rr_1_0_achieved"]
        )
        doc_ref.set(analysis_data.model_dump())
        logger.info(f"[CREATE_GHOST][{trade_id}] SUKCES! Utworzono 'ducha'.")
    except Exception as e:
        logger.error(f"[CREATE_GHOST][{trade_id}] KRYTYCZNY BŁĄD: {e}", exc_info=True)

def update_analyzed_trade_state(trade_id: str, new_extreme_price: float, new_timestamp_ms: int):
    doc_ref = _get_db().collection(constants.ANALYZED_COLLECTION).document(trade_id)
    update_data = {
        "last_known_extreme_price": new_extreme_price,
        "last_analysis_timestamp_ms": new_timestamp_ms
    }
    doc_ref.update(update_data)
    logger.info(f"[{trade_id}] Zaktualizowano stan 'ducha'. Nowa cena: {new_extreme_price}")

def remove_analyzed_trade(trade_id: str):
    _get_db().collection(constants.ANALYZED_COLLECTION).document(trade_id).delete()
    logger.info(f"[{trade_id}] Zakończono i usunięto 'ducha'.")


def get_all_analyzed_trades() -> Iterable[DocumentSnapshot]:
    """Pobiera wszystkie dokumenty 'duchów' z dodatkowym logowaniem diagnostycznym."""
    logger.info("[DIAGNOSTYKA DUCHA] Próba pobrania dokumentów z kolekcji 'analyzed_trades'...")
    try:
        collection_ref = _get_db().collection(constants.ANALYZED_COLLECTION)
        docs_stream = collection_ref.stream()
        
        # Konwertujemy iterator na listę, aby policzyć elementy i uniknąć wyczerpania iteratora
        docs_list = list(docs_stream) 
        
        logger.info(f"[DIAGNOSTYKA DUCHA] Pomyślnie pobrano {len(docs_list)} dokumentów z 'analyzed_trades'.")
        return docs_list
    except Exception as e:
        logger.error(f"[DIAGNOSTYKA DUCHA] KRYTYCZNY BŁĄD podczas pobierania duchów: {e}", exc_info=True)
        # Zwracamy pustą listę w przypadku błędu, aby nie zatrzymać całego cyklu
        return []
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
    """
    Atomowo usuwa otwartą pozycję z kolekcji 'open_trades' i aktualizuje
    powiązany z nią dokument w 'active_setups'.
    """
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