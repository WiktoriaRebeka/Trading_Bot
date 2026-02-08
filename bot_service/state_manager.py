# Lokalizacja: bot_service/state_manager.py
import logging
from typing import Dict, Any, Iterable, Optional
from google.cloud import firestore
from google.api_core.exceptions import GoogleAPICallError, Aborted
from datetime import datetime, timezone
from shared_lib.firebase_client import get_db
logger = logging.getLogger(__name__)


def _get_client() -> firestore.Client:
    """
    V7.6: Pobiera poprawnie skonfigurowanego klienta z shared_lib.
    Eliminuje błąd NameError oraz 404 Database Not Found.
    """
    return get_db()
# -------------------------
# Basic helpers
# -------------------------
def _collection_active_orders():
    return _get_client().collection("active_orders")

# -------------------------
# Save with transaction (atomic set/update)
# -------------------------
def save_active_order_transactional(order_link_id: str, payload: Dict[str, Any]) -> None:
    """
    Transactional set/update of an active order document.
    If document exists -> update (merge), else set.
    """
    client = _get_client()
    doc_ref = _collection_active_orders().document(order_link_id)
    transaction = client.transaction()

    @firestore.transactional
    def _txn_set(txn, ref, data):
        snap = ref.get(transaction=txn)
        if snap.exists:
            txn.update(ref, data)
        else:
            txn.set(ref, data)

    try:
        _txn_set(transaction, doc_ref, payload)
    except Aborted as e:
        logger.exception(f"[state_manager] Transaction aborted for {order_link_id}: {e}")
        raise
    except GoogleAPICallError as e:
        logger.exception(f"[state_manager] Firestore API error saving {order_link_id}: {e}")
        raise


def save_active_order(order_link_id: str, payload: Dict[str, Any]) -> None:
    """
    Non-transactional save (fallback). Uses set(merge=True) to avoid overwriting.
    """
    try:
        doc_ref = _collection_active_orders().document(order_link_id)
        doc_ref.set(payload, merge=True)
    except Exception as e:
        logger.exception(f"[state_manager] Failed to save active order {order_link_id}: {e}")
        raise


def update_active_order(order_link_id: str, updates: Dict[str, Any]) -> None:
    try:
        doc_ref = _collection_active_orders().document(order_link_id)
        doc_ref.update(updates)
    except Exception as e:
        logger.exception(f"[state_manager] Failed to update active order {order_link_id}: {e}")
        raise


def delete_active_order_by_id(order_link_id: str) -> None:
    try:
        doc_ref = _collection_active_orders().document(order_link_id)
        doc_ref.delete()
    except Exception as e:
        logger.exception(f"[state_manager] Failed to delete active order {order_link_id}: {e}")
        raise


# -------------------------
# Query helpers used by bot_logic
# -------------------------
def get_orders_by_status(status: str) -> Iterable[firestore.DocumentSnapshot]:
    try:
        q = _collection_active_orders().where("status", "==", status)
        return list(q.stream())
    except Exception as e:
        logger.exception(f"[state_manager] Failed to query orders by status {status}: {e}")
        return []


def get_orders_without_status() -> Iterable[firestore.DocumentSnapshot]:
    try:
        # Firestore doesn't support "where field not exists" directly; use a safe fallback:
        q = _collection_active_orders().where("status", "==", None)
        return list(q.stream())
    except Exception:
        # fallback: return empty list
        return []


def get_active_order_by_id(order_link_id: str) -> Optional[Dict[str, Any]]:
    try:
        doc = _collection_active_orders().document(order_link_id).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        logger.exception(f"[state_manager] Failed to get active order by id {order_link_id}: {e}")
        return None


def get_active_order_by_sl_order_id(sl_order_id: str) -> Optional[Dict[str, Any]]:
    try:
        q = _collection_active_orders().where("slOrderId", "==", sl_order_id).limit(1)
        docs = list(q.stream())
        if docs:
            return docs[0].to_dict()
        return None
    except Exception as e:
        logger.exception(f"[state_manager] Failed to get active order by slOrderId {sl_order_id}: {e}")
        return None


def find_active_order_by_details(symbol: str, side: str, qty: float) -> Optional[Dict[str, Any]]:
    """
    Best-effort search by symbol/side/qty. This is heuristic and may return None.
    """
    try:
        q = _collection_active_orders().where("symbol", "==", symbol).where("direction", "==", side).limit(10)
        for doc in q.stream():
            d = doc.to_dict()
            planned_qty = d.get("planned_qty")
            try:
                if planned_qty is not None and abs(float(planned_qty) - float(qty)) < 1e-8:
                    return d
            except Exception:
                continue
        return None
    except Exception as e:
        logger.exception(f"[state_manager] Failed to find active order by details for {symbol}: {e}")
        return None


def get_latest_active_order_for_symbol(symbol: str, side: str) -> Optional[Dict[str, Any]]:
    try:
        q = _collection_active_orders().where("symbol", "==", symbol).where("direction", "==", side).order_by("created_at", direction=firestore.Query.DESCENDING).limit(1)
        docs = list(q.stream())
        if docs:
            return docs[0].to_dict()
        return None
    except Exception as e:
        logger.exception(f"[state_manager] Failed to get latest active order for {symbol}: {e}")
        return None


# -------------------------
# PnL dedup helpers
# -------------------------
def is_pnl_record_processed(order_id: str) -> bool:
    try:
        q = _get_client().collection("pnl_processed").document(str(order_id)).get()
        return q.exists
    except Exception as e:
        logger.exception(f"[state_manager] Failed to check pnl processed for {order_id}: {e}")
        return False


# -------------------------
# Utility: for tests / admin
# -------------------------
def delete_all_active_orders_for_symbol(symbol: str) -> None:
    try:
        q = _collection_active_orders().where("symbol", "==", symbol)
        for doc in q.stream():
            doc.reference.delete()
    except Exception as e:
        logger.exception(f"[state_manager] Failed to delete orders for symbol {symbol}: {e}")