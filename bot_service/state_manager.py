# Lokalizacja: bot_service/state_manager.py

import logging
from typing import Iterable, Dict, Any, List, Optional
from datetime import datetime, timezone
from google.cloud import firestore
from google.cloud.firestore_v1.document import DocumentSnapshot

from shared_lib.firebase_client import get_db
from shared_lib import constants
from shared_lib.models import AnalyticalCase

logger = logging.getLogger(__name__)

def _get_db() -> firestore.Client:
    return get_db()

def get_pending_case_for_symbol(symbol: str) -> Optional[DocumentSnapshot]:
    try:
        docs = _get_db().collection(constants.ANALYTICAL_CASES_COLLECTION).where('symbol', '==', symbol).where('status', '==', 'PENDING').limit(1).stream()
        return next(docs, None)
    except Exception as e:
        logger.error(f"Błąd podczas pobierania teczki PENDING dla {symbol}: {e}", exc_info=True)
        return None

def delete_case_by_id(case_id: str):
    try:
        _get_db().collection(constants.ANALYTICAL_CASES_COLLECTION).document(case_id).delete()
        logger.info(f"[{case_id}] Pomyślnie usunięto teczkę z Firestore.")
    except Exception as e:
        logger.error(f"Błąd podczas usuwania teczki {case_id}: {e}", exc_info=True)

def create_analytical_case(case_data: AnalyticalCase):
    try:
        doc_ref = _get_db().collection(constants.ANALYTICAL_CASES_COLLECTION).document(case_data.alert_id)
        data_to_set = case_data.model_dump(mode='json')
        doc_ref.set(data_to_set)
        logger.info(f"[{case_data.alert_id}] Utworzono nową teczkę analityczną dla {case_data.symbol}.")
    except Exception as e:
        logger.error(f"Błąd podczas tworzenia teczki {case_data.alert_id}: {e}", exc_info=True)

def get_all_analytical_cases() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.ANALYTICAL_CASES_COLLECTION).stream()

def update_case_status_and_results(case_id: str, updates: Dict[str, Any]):
    try:
        doc_ref = _get_db().collection(constants.ANALYTICAL_CASES_COLLECTION).document(case_id)
        doc_ref.update(updates)
        logger.info(f"[{case_id}] Zaktualizowano teczkę z danymi: {updates}")
    except Exception as e:
        logger.error(f"Błąd podczas aktualizacji teczki {case_id}: {e}", exc_info=True)

def get_latest_klines_from_cache(symbols: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    if not symbols: return {}
    db = _get_db()
    klines_cache = {}
    unique_symbols = list(set(s for s in symbols if isinstance(s, str) and s))
    if not unique_symbols: return {}
    for i in range(0, len(unique_symbols), 30):
        chunk = unique_symbols[i:i + 30]
        if not chunk: continue
        try:
            docs = db.collection(constants.LATEST_KLINES_COLLECTION).where("symbol", "in", chunk).stream()
            for doc in docs:
                klines_cache[doc.id] = doc.to_dict()
        except Exception as e:
            logger.error(f"[KLINE_CACHE] Błąd podczas pobierania danych dla części {chunk}: {e}", exc_info=True)
            continue
    if klines_cache:
        logger.info(f"[KLINE_CACHE] Pomyślnie pobrano {len(klines_cache)} rekordów kline z cache'u.")
    else:
        logger.warning(f"[KLINE_CACHE] Nie udało się pobrać rekordów kline z cache'u dla {unique_symbols}.")
    return klines_cache

def save_active_order(order_link_id: str, order_data: Dict[str, Any]):
    """Zapisuje informacje o aktywnym zleceniu, używając orderLinkId jako ID dokumentu."""
    try:
        if not order_link_id:
            logger.error("Próba zapisu aktywnego zlecenia bez orderLinkId.")
            return
        
        doc_ref = _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).document(order_link_id)
        order_data['created_at'] = datetime.now(timezone.utc)
        doc_ref.set(order_data)
        logger.info(f"Zapisano aktywne zlecenie {order_link_id} dla symbolu {order_data.get('symbol')}.")
    except Exception as e:
        logger.error(f"Błąd podczas zapisu aktywnego zlecenia {order_link_id}: {e}", exc_info=True)
        
def get_active_order_by_id(order_link_id: str) -> Optional[Dict[str, Any]]:
    """Pobiera dane aktywnego zlecenia na podstawie jego orderLinkId (który jest ID dokumentu)."""
    try:
        doc_ref = _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).document(order_link_id)
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            data['id'] = doc.id # Dodajemy ID dokumentu (czyli orderLinkId) do zwracanych danych
            return data
        return None
    except Exception as e:
        logger.error(f"Błąd podczas pobierania aktywnego zlecenia {order_link_id}: {e}", exc_info=True)
        return None

def get_active_order_by_limit_order_id(limit_order_id: str) -> Optional[Dict[str, Any]]:
    """Wyszukuje aktywny dokument zlecenia na podstawie pola limitOrderId."""
    try:
        docs = _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).where('limitOrderId', '==', limit_order_id).limit(1).stream()
        doc_snapshot = next(docs, None)
        if doc_snapshot:
            data = doc_snapshot.to_dict()
            data['id'] = doc_snapshot.id # Dodajemy ID dokumentu (czyli orderLinkId) do zwracanych danych
            return data
        return None
    except Exception as e:
        logger.error(f"Błąd podczas wyszukiwania zlecenia po limitOrderId {limit_order_id}: {e}", exc_info=True)
        return None

def delete_active_order_by_id(order_link_id: str):
    """Usuwa dokument aktywnego zlecenia na podstawie jego orderLinkId."""
    try:
        _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).document(order_link_id).delete()
        logger.info(f"[{order_link_id}] Pomyślnie usunięto przetworzone zlecenie z kolekcji active_orders.")
    except Exception as e:
        logger.error(f"Błąd podczas usuwania aktywnego zlecenia {order_link_id}: {e}", exc_info=True)


def get_alert_data_by_id(alert_id: str) -> Optional[Dict[str, Any]]:
    try:
        doc_ref = _get_db().collection(constants.FIRESTORE_COLLECTION_ALERTS).document(alert_id)
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict()
        logger.warning(f"Nie znaleziono dokumentu alertu o ID: {alert_id}")
        return None
    except Exception as e:
        logger.error(f"Błąd podczas pobierania danych alertu {alert_id}: {e}", exc_info=True)
        return None

def get_orders_by_status(status: str) -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).where('status', '==', status).stream()

def update_active_order(order_id: str, updates: Dict[str, Any]):
    try:
        doc_ref = _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).document(order_id)
        doc_ref.update(updates)
        logger.info(f"Zaktualizowano aktywne zlecenie {order_id} z danymi: {updates}")
    except Exception as e:
        logger.error(f"Błąd podczas aktualizacji zlecenia {order_id}: {e}", exc_info=True)