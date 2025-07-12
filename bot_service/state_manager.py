# Lokalizacja: bot_service/state_manager.py
import logging
from typing import Iterable, Dict, Any, Optional
from datetime import datetime, timezone

from google.cloud import firestore
from google.cloud.firestore_v1.document import DocumentSnapshot

from shared_lib.firebase_client import get_db
from shared_lib import constants
from shared_lib.models import AlertData, OpenTradeData, AnalyzedTradeData

logger = logging.getLogger(__name__)

def _get_db() -> firestore.Client:
    return get_db()

# --- Funkcje zarządzania setupami i pozycjami ---

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

def get_all_open_trades() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.TRADE_COLLECTION).stream()

# Lokalizacja: bot_service/state_manager.py

def create_open_trade(trade_id: str, symbol: str, direction: str, ob_type: str, entry_price: float, sl_price: float, tp_price: float, alert_data: AlertData):
    db = _get_db()
    
    # Używamy transakcji, aby zapewnić atomowość operacji
    transaction = db.transaction()
    
    @firestore.transactional
    def _create_trade_in_transaction(transaction, trade_id, symbol, direction, ob_type, entry_price, sl_price, tp_price, alert_data):
        # Referencje do dokumentów
        trade_doc_ref = db.collection(constants.TRADE_COLLECTION).document(trade_id)
        setup_doc_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)

        # Sprawdzenie, czy setup wciąż istnieje i nie ma otwartej pozycji
        # To dodatkowe zabezpieczenie przed race condition
        setup_snapshot = setup_doc_ref.get(transaction=transaction)
        if not setup_snapshot.exists:
            raise RuntimeError(f"Setup dla symbolu {symbol} już nie istnieje. Anulowano tworzenie pozycji.")
        
        if setup_snapshot.to_dict().get("is_position_open_on_this_setup", False):
            raise RuntimeError(f"Pozycja dla setupu {symbol} jest już oznaczona jako otwarta. Anulowano tworzenie zduplikowanej pozycji.")

        # 1. Utwórz nową pozycję
        timestamp_utc = datetime.now(timezone.utc)
        new_trade = OpenTradeData(
            trade_id=trade_id, symbol=symbol, direction=direction.upper(), ob_type=ob_type,
            entry_price=entry_price, sl_price=sl_price, tp_price=tp_price,
            opened_at_ms=int(timestamp_utc.timestamp() * 1000),
            opened_at_iso=timestamp_utc.isoformat(),
            alert_data_snapshot=alert_data.model_dump(by_alias=True)
        )
        transaction.set(trade_doc_ref, new_trade.model_dump())

        # 2. Zaktualizuj setup
        update_data = {
            "is_position_open_on_this_setup": True,
            "entry_attempts": firestore.Increment(1)
        }
        transaction.update(setup_doc_ref, update_data)
        
        logger.info(f"[{symbol}][{trade_id}] Transakcja przygotowana: utworzenie pozycji i aktualizacja setupu.")

    try:
        _create_trade_in_transaction(transaction, trade_id, symbol, direction, ob_type, entry_price, sl_price, tp_price, alert_data)
        logger.info(f"[{symbol}][{trade_id}] SUKCES. Transakcja atomowa zakończona pomyślnie.")
    except Exception as e:
        logger.error(f"[{symbol}][{trade_id}] BŁĄD TRANSAKCJI. Nie udało się atomowo utworzyć pozycji: {e}", exc_info=True)
        # Rzucamy wyjątek dalej, aby logika wywołująca mogła zareagować
        raise

def remove_open_trade(trade_id: str):
    _get_db().collection(constants.TRADE_COLLECTION).document(trade_id).delete()
    logger.info(f"[{trade_id}] Usunięto pozycję z aktywnego monitorowania.")

def get_all_analyzed_trades() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.ANALYZED_COLLECTION).stream()

def create_analyzed_trade(trade_data: OpenTradeData):
    db = _get_db()
    trade_id = trade_data.trade_id
    if not trade_id: return
    
    doc_ref = db.collection(constants.ANALYZED_COLLECTION).document(trade_id)
    
    # Poprawione i bezpieczne pobieranie wartości
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
        # Inicjalizacja nowych pól
        last_known_extreme_price=trade_data.tp_price, # Początkowa cena ekstremalna to TP1
        last_analysis_timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
        achieved_tps=["rr_1_0_achieved"] # Zawsze osiągamy co najmniej TP1
    )
    doc_ref.set(analysis_data.model_dump())
    logger.info(f"[{trade_id}] Utworzono 'ducha' dla transakcji WIN do analizy post-mortem.")

def update_analyzed_trade_state(trade_id: str, new_extreme_price: float, new_timestamp_ms: int, new_achieved_tps: list):
    doc_ref = _get_db().collection(constants.ANALYZED_COLLECTION).document(trade_id)
    update_data = {
        "last_known_extreme_price": new_extreme_price,
        "last_analysis_timestamp_ms": new_timestamp_ms,
        "achieved_tps": new_achieved_tps
    }
    doc_ref.update(update_data)
    logger.info(f"[{trade_id}] Zaktualizowano stan 'ducha'. Nowa cena ekstremalna: {new_extreme_price}")

def remove_analyzed_trade(trade_id: str):
    _get_db().collection(constants.ANALYZED_COLLECTION).document(trade_id).delete()
    logger.info(f"[{trade_id}] Zakończono i usunięto pozycję z analizy post-mortem.")

def get_latest_klines_from_cache(symbols: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    if not symbols: return {}
    db = _get_db()
    klines_cache = {}
    unique_symbols = list(set(s for s in symbols if isinstance(s, str) and s))
    if not unique_symbols:
        logger.warning("Lista symboli po wstępnym przefiltrowaniu jest pusta.")
        return {}
    # Zwiększono limit do 30 (maksymalny dla 'in' w Firestore)
    for i in range(0, len(unique_symbols), 30):
        chunk = unique_symbols[i:i + 30]
        if not chunk: continue
        try:
            docs = db.collection(constants.LATEST_KLINES_COLLECTION).where("__name__", "in", chunk).stream()
            for doc in docs:
                klines_cache[doc.id] = doc.to_dict()
        except Exception as e:
            logger.error(f"Błąd podczas pobierania danych kline z cache'u dla chunk'a: {chunk}. Błąd: {e}", exc_info=True)
    if klines_cache:
        logger.info(f"Pobrano {len(klines_cache)} rekordów kline z cache'u w Firestore.")
    else:
        logger.warning("Nie udało się pobrać żadnych rekordów kline z cache'u. Sprawdź, czy kolekcja '%s' zawiera dokumenty o podanych ID.", constants.LATEST_KLINES_COLLECTION)
    return klines_cache

