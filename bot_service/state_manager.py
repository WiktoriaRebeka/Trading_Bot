# Lokalizacja: bot_service/state_manager.py

import logging
from typing import Iterable, Dict, Any, List
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

def get_all_analyzed_trades() -> Iterable[DocumentSnapshot]:
    logger.info("[DIAGNOSTYKA DUCHA] Próba pobrania dokumentów z 'analyzed_trades'...")
    try:
        collection_ref = _get_db().collection(constants.ANALYZED_COLLECTION)
        docs_stream = collection_ref.stream()
        docs_list = list(docs_stream) 
        logger.info(f"[DIAGNOSTYKA DUCHA] Pomyślnie pobrano {len(docs_list)} dokumentów z 'analyzed_trades'.")
        return docs_list
    except Exception as e:
        logger.error(f"[DIAGNOSTYKA DUCHA] KRYTYCZNY BŁĄD podczas pobierania duchów: {e}", exc_info=True)
        return []

def create_analyzed_trade(trade_data: OpenTradeData):
    """Tworzy 'ducha' dla transakcji WIN do analizy post-mortem."""
    db = _get_db()
    trade_id = trade_data.trade_id
    
    if not trade_id:
        logger.error("[CREATE_GHOST] Otrzymano dane transakcji bez trade_id.")
        return

    logger.info(f"[CREATE_GHOST][{trade_id}] Rozpoczynam tworzenie 'ducha' dla transakcji WIN.")
    
    try:
        doc_ref = db.collection(constants.ANALYZED_COLLECTION).document(trade_id)
        
        tp5_value = trade_data.alert_data_snapshot.get('tp_5_0')
        
        analysis_data = AnalyzedTradeData(
            trade_id=trade_id,
            symbol=trade_data.symbol,
            direction=trade_data.direction,
            ob_type=trade_data.ob_type,  # <-- DODANA LINIA
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
        logger.error(f"[CREATE_GHOST][{trade_id}] KRYTYCZNY BŁĄD podczas tworzenia 'ducha': {e}", exc_info=True)

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
            logger.debug(f"[KLINE_CACHE] Przetwarzam część: {chunk}")
            docs = db.collection(constants.LATEST_KLINES_COLLECTION).where("__name__", "in", chunk).stream()
            chunk_results = 0
            for doc in docs:
                klines_cache[doc.id] = doc.to_dict()
                chunk_results += 1
            logger.debug(f"[KLINE_CACHE] Pomyślnie pobrano {chunk_results} dokumentów dla tej części.")
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


def get_historical_klines(symbol: str, start_time_ms: int, end_time_ms: int) -> List[Dict[str, Any]]:
    """
    Pobiera historyczne świece 1-minutowe z API Bybit.
    UWAGA: Ta funkcja wykonuje zapytanie sieciowe i nie korzysta z cache'u.
    """
    import requests 
    
    logger.info(f"[{symbol}] Pobieranie historii świec od {start_time_ms} do {end_time_ms}")
    klines = []
    api_symbol = symbol.replace('.P', '')
    
    params = {
        "category": "linear",
        "symbol": api_symbol,
        "interval": "1",
        "start": start_time_ms,
        "end": end_time_ms,
        "limit": 1000
    }
    try:

        kline_endpoint = "/v5/market/kline"
        full_url = constants.BYBIT_API_URL_V5 + kline_endpoint
        response = requests.get(full_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
           
            kline_list = reversed(data["result"]["list"])
            for k in kline_list:
                klines.append({
                    "timestamp": int(k[0]),
                    "high": float(k[2]),
                    "low": float(k[3])
                })
            logger.info(f"[{symbol}] Pomyślnie pobrano {len(klines)} historycznych świec.")
            return klines
        else:
            logger.error(f"[{symbol}] Błąd API Bybit podczas pobierania historii: {data.get('retMsg')}")
    except Exception as e:
        logger.error(f"[{symbol}] Krytyczny błąd podczas pobierania historii świec: {e}", exc_info=True)
        
    return []

# Należy dodać te dwie funkcje do pliku state_manager.py

def get_open_trade_by_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Wyszukuje w kolekcji 'open_trades' dokument dla danego symbolu."""
    try:
        db = get_db()
        trades_ref = db.collection('open_trades').where('symbol', '==', symbol).limit(1).stream()
        for trade_doc in trades_ref:
            # Zwracamy słownik, aby mieć dostęp do trade_id (które jest ID dokumentu)
            trade_data = trade_doc.to_dict()
            trade_data['trade_id'] = trade_doc.id
            return trade_data
        return None
    except Exception as e:
        logger.error(f"Błąd podczas wyszukiwania otwartego zlecenia dla symbolu {symbol}: {e}")
        return None

def delete_open_trade(trade_id: str):
    """Usuwa dokument z kolekcji 'open_trades' na podstawie jego ID."""
    try:
        db = get_db()
        db.collection('open_trades').document(trade_id).delete()
        logger.info(f"Pomyślnie usunięto dokument zlecenia {trade_id} z Firestore.")
    except Exception as e:
        logger.error(f"Błąd podczas usuwania dokumentu zlecenia {trade_id}: {e}")