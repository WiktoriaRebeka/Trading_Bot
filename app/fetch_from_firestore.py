# TRADING_BOT/app/fetch_from_firestore.py
import firebase_admin
from firebase_admin import firestore
from datetime import datetime, timezone, timedelta
import logging

from .firebase_client import get_db 
from .constants import (
    FIRESTORE_COLLECTION_ALERTS,
    BOT_CONFIG_COLLECTION,
    LAST_FETCH_STATE_DOC_ID,
    LAST_PROCESSED_TS_FIELD
)

logger = logging.getLogger(__name__)

def load_last_processed_timestamp() -> datetime:
    """Odczytuje ostatni timestamp z Firestore jako obiekt datetime."""
    db = get_db()
    try:
        doc_ref = db.collection(BOT_CONFIG_COLLECTION).document(LAST_FETCH_STATE_DOC_ID)
        doc = doc_ref.get()
        if doc.exists:
            # Firestore zwraca timestamp jako obiekt datetime z tzinfo
            timestamp = doc.get(LAST_PROCESSED_TS_FIELD)
            if timestamp:
                logger.info(f"[FETCHER] Odczytano ostatni timestamp z Firestore: {timestamp.isoformat()}")
                return timestamp
    except Exception as e:
        logger.error(f"[FETCHER_ERROR] Nie udało się odczytać timestampa z Firestore: {e}", exc_info=True)
    
    # Domyślna wartość, jeśli nic nie ma w bazie lub wystąpił błąd
    fallback_ts = datetime.now(timezone.utc) - timedelta(days=1)
    logger.warning(f"[FETCHER] Nie znaleziono timestampa w Firestore, używam wartości domyślnej: {fallback_ts.isoformat()}")
    return fallback_ts


def save_last_processed_timestamp(timestamp_dt: datetime):
    """Zapisuje nowy timestamp (jako obiekt datetime) do Firestore."""
    db = get_db()
    try:
        doc_ref = db.collection(BOT_CONFIG_COLLECTION).document(LAST_FETCH_STATE_DOC_ID)
        doc_ref.set({LAST_PROCESSED_TS_FIELD: timestamp_dt}, merge=True)
        logger.info(f"[FETCHER] Zapisano nowy timestamp do Firestore: {timestamp_dt.isoformat()}")
    except Exception as e:
        logger.error(f"[FETCHER_ERROR] Nie udało się zapisać timestampu {timestamp_dt.isoformat()} do Firestore: {e}", exc_info=True)


def fetch_new_alerts_since(last_ts_dt: datetime):
    """
    Pobiera nowe alerty z Firestore i zwraca je wraz z najnowszym timestampem.
    """
    db = get_db()
    new_alerts_list = []
    
    # Inicjujemy maksymalny timestamp wartością początkową
    new_max_ts = last_ts_dt 
    
    try:
        # Tworzymy zapytanie do Firestore
        # Szukamy alertów, gdzie 'received_at' jest nowsze niż nasz ostatni zapisany timestamp
        # i sortujemy je, aby przetwarzać w kolejności
        query = db.collection(FIRESTORE_COLLECTION_ALERTS) \
                  .where(filter=firestore.FieldFilter('received_at', '>', last_ts_dt)) \
                  .order_by('received_at', direction=firestore.Query.ASCENDING)
        
        docs = query.stream()

        for doc in docs:
            alert_data = doc.to_dict()
            alert_data['id'] = doc.id # Dodajemy ID dokumentu dla celów logowania
            new_alerts_list.append(alert_data)
            
            # Aktualizujemy najnowszy znaleziony timestamp
            current_doc_ts = alert_data.get('received_at')
            if current_doc_ts and current_doc_ts > new_max_ts:
                new_max_ts = current_doc_ts
        
        if new_alerts_list:
            logger.info(f"[FETCHER] Pobrano {len(new_alerts_list)} nowych alertów.")
        else:
            logger.info("[FETCHER] Brak nowych alertów od ostatniego sprawdzenia.")
            
    except firebase_admin.exceptions.FirebaseError as fb_err:
        logger.error(f"[FETCHER_FIRESTORE_ERROR] Błąd Firebase podczas pobierania alertów: {fb_err}", exc_info=True)
    except Exception as e:
        logger.error(f"[FETCHER_FIRESTORE_ERROR] Inny błąd podczas pobierania alertów: {e}", exc_info=True)
    
    # Zwracamy listę alertów i najnowszy timestamp jako obiekt datetime
    return new_alerts_list, new_max_ts

# Poniższa część pliku (fetcher_loop) nie jest używana w architekturze z Cloud Scheduler,
# więc może pozostać bez zmian lub zostać usunięta dla czystości.
def fetcher_loop():
    pass