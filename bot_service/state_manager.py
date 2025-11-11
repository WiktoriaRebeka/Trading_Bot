# Lokalizacja: bot_service/state_manager.py


import logging
from typing import Iterable, Dict, Any, Optional
from datetime import datetime, timezone
from google.cloud import firestore
from google.cloud.firestore_v1.document import DocumentSnapshot
import math

from shared_lib.firebase_client import get_db
from shared_lib import constants
from shared_lib.models import AnalyticalCase

logger = logging.getLogger(__name__)

def _get_db() -> firestore.Client:
    return get_db()

def save_active_order(order_link_id: str, order_data: Dict[str, Any]):
    try:
        doc_ref = _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).document(order_link_id)
        order_data['created_at'] = datetime.now(timezone.utc)
        order_data['status'] = 'PLACED'
        doc_ref.set(order_data)
    except Exception as e:
        logger.error(f"Błąd podczas zapisu aktywnego zlecenia {order_link_id}: {e}", exc_info=True)
        
def get_active_order_by_id(order_link_id: str) -> Optional[Dict[str, Any]]:
    try:
        doc = _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).document(order_link_id).get()
        if doc.exists:
            data = doc.to_dict()
            data['id'] = doc.id
            return data
        return None
    except Exception as e:
        logger.error(f"Błąd podczas pobierania aktywnego zlecenia {order_link_id}: {e}", exc_info=True)
        return None

def delete_active_order_by_id(order_link_id: str):
    try:
        _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).document(order_link_id).delete()
    except Exception as e:
        logger.error(f"Błąd podczas usuwania aktywnego zlecenia {order_link_id}: {e}", exc_info=True)

def get_orders_by_status(status: str) -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).where('status', '==', status).stream()

def get_orders_without_status() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).where('status', '==', None).stream()

def update_active_order(order_id: str, updates: Dict[str, Any]):
    try:
        _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).document(order_id).update(updates)
    except Exception as e:
        logger.error(f"Błąd podczas aktualizacji zlecenia {order_id}: {e}", exc_info=True)

def get_active_order_by_sl_order_id(sl_order_id: str) -> Optional[Dict[str, Any]]:
    try:
        query_sl = _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).where('slOrderId', '==', sl_order_id).limit(1)
        docs_sl = list(query_sl.stream())
        if docs_sl:
            doc_snapshot = docs_sl[0]
            data = doc_snapshot.to_dict()
            data['id'] = doc_snapshot.id
            return data
        return None
    except Exception as e:
        logger.error(f"Błąd podczas wyszukiwania zlecenia po sl_order_id {sl_order_id}: {e}", exc_info=True)
        return None


def find_active_order_by_details(symbol: str, side: str, qty: float) -> Optional[Dict[str, Any]]:
    """Wyszukuje aktywne zlecenie na podstawie symbolu, kierunku i ilości z tolerancją."""
    try:
        docs = _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION) \
            .where('symbol', '==', symbol) \
            .where('direction', '==', side) \
            .stream()
        
        # Definiujemy małą tolerancję, np. 0.1% wielkości pozycji
        QTY_TOLERANCE_PERCENT = 0.001 

        for doc in docs:
            order_data = doc.to_dict()
            planned_qty = order_data.get('planned_qty', 0.0)
            
            # Obliczamy dopuszczalną różnicę
            tolerance = planned_qty * QTY_TOLERANCE_PERCENT
            
            # Sprawdzamy, czy różnica mieści się w tolerancji
            if abs(planned_qty - qty) <= tolerance:
                logger.info(f"Znaleziono dopasowanie po szczegółach (z tolerancją) dla {symbol}. Planowane Qty: {planned_qty}, Rzeczywiste Qty: {qty}")
                order_data['id'] = doc.id
                return order_data
        return None
    except Exception as e:
        logger.error(f"Błąd podczas dopasowania po szczegółach transakcji dla {symbol}: {e}", exc_info=True)
        return None

def get_latest_active_order_for_symbol(symbol: str, side: str) -> Optional[Dict[str, Any]]:
    try:
        query = _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION) \
                  .where('symbol', '==', symbol) \
                  .where('direction', '==', side) \
                  .order_by('created_at', direction=firestore.Query.DESCENDING) \
                  .limit(1)
        docs = list(query.stream())
        if docs:
            data = docs[0].to_dict()
            data['id'] = docs[0].id
            return data
        return None
    except Exception as e:
        logger.error(f"Błąd podczas awaryjnego dopasowania zlecenia dla {symbol}/{side}: {e}", exc_info=True)
        return None

def get_all_active_orders() -> Iterable[DocumentSnapshot]:
    return _get_db().collection(constants.ACTIVE_ORDERS_COLLECTION).stream()

def is_alert_processed(alert_id: str) -> bool:
    try:
        return _get_db().collection(constants.PROCESSED_ALERT_IDS_COLLECTION).document(alert_id).get().exists
    except Exception:
        return True

def mark_alert_as_processed(alert_id: str):
    try:
        _get_db().collection(constants.PROCESSED_ALERT_IDS_COLLECTION).document(alert_id).set({'processed_at': datetime.now(timezone.utc)})
    except Exception as e:
        logger.error(f"Błąd podczas oznaczania alertu {alert_id} jako przetworzony: {e}", exc_info=True)

def is_pnl_record_processed(order_id: str) -> bool:
    if not order_id: return True
    try:
        return _get_db().collection(constants.PROCESSED_ORDER_IDS_COLLECTION).document(order_id).get().exists
    except Exception:
        return True