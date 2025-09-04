# Lokalizacja: bot_service/fetch_from_firestore.py
from google.cloud import firestore
from datetime import datetime, timezone, timedelta
import logging
from google.cloud.firestore_v1.base_query import FieldFilter
from shared_lib.firebase_client import get_db 
from shared_lib.constants import (
    FIRESTORE_COLLECTION_ALERTS,
    BOT_CONFIG_COLLECTION,
    LAST_FETCH_STATE_DOC_ID,
    LAST_PROCESSED_TS_FIELD
)

logger = logging.getLogger(__name__)

def load_last_processed_timestamp() -> datetime:
    db = get_db()
    try:
        doc_ref = db.collection(BOT_CONFIG_COLLECTION).document(LAST_FETCH_STATE_DOC_ID)
        doc = doc_ref.get()
        if doc.exists:
            timestamp = doc.get(LAST_PROCESSED_TS_FIELD)
            if timestamp:
                logger.info(f"[FETCHER] Odczytano ostatni timestamp z Firestore: {timestamp.isoformat()}")
                return timestamp
    except Exception as e:
        logger.error(f"[FETCHER_ERROR] Nie udało się odczytać timestampa z Firestore: {e}", exc_info=True)
    fallback_ts = datetime.now(timezone.utc) - timedelta(days=1)
    logger.warning(f"[FETCHER] Nie znaleziono timestampa w Firestore, używam wartości domyślnej: {fallback_ts.isoformat()}")
    return fallback_ts

def save_last_processed_timestamp(timestamp_dt: datetime):
    db = get_db()
    try:
        doc_ref = db.collection(BOT_CONFIG_COLLECTION).document(LAST_FETCH_STATE_DOC_ID)
        doc_ref.set({LAST_PROCESSED_TS_FIELD: timestamp_dt}, merge=True)
        logger.info(f"[FETCHER] Zapisano nowy timestamp do Firestore: {timestamp_dt.isoformat()}")
    except Exception as e:
        logger.error(f"[FETCHER_ERROR] Nie udało się zapisać timestampu {timestamp_dt.isoformat()} do Firestore: {e}", exc_info=True)

def fetch_new_alerts_since(last_ts_dt: datetime):
    db = get_db()
    new_alerts_list = []
    new_max_ts = last_ts_dt
    try:
        # --- POPRAWKA OSTRZEŻENIA FIRESTORE ---
        query = db.collection(FIRESTORE_COLLECTION_ALERTS) \
            .where(filter=FieldFilter('received_at', '>', last_ts_dt)) \
            .order_by('received_at')
        
        docs = query.stream()
        for doc in docs:
            alert_data = doc.to_dict()
            alert_data['id'] = doc.id
            new_alerts_list.append(alert_data)
            current_doc_ts = alert_data.get('received_at')
            if current_doc_ts and current_doc_ts > new_max_ts:
                new_max_ts = current_doc_ts
        
        if new_alerts_list:
            logger.info(f"[FETCHER] Pobrano {len(new_alerts_list)} nowych alertów.")
        else:
            logger.info("[FETCHER] Brak nowych alertów od ostatniego sprawdzenia.")
            
    except Exception as e:
        logger.error(f"[FETCHER_FIRESTORE_ERROR] Błąd podczas pobierania alertów: {e}", exc_info=True)
        
    return new_alerts_list, new_max_ts