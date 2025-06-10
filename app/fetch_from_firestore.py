# TRADING_BOT/app/fetch_from_firestore.py
import firebase_admin # Dla firebase_admin.exceptions lub typów, jeśli potrzebne
from firebase_admin import firestore # Dla firestore.FieldFilter, firestore.Query
import time
import os
from datetime import datetime, timezone, timedelta
import traceback
import logging # Użyj logging zamiast sys.stderr

# Importuj funkcję get_db z nowego modułu
from .firebase_client import get_db 

from . import state_manager 
from .constants import (
    FETCH_INTERVAL_SECONDS,
    FIRESTORE_COLLECTION_ALERTS,
    BOT_CONFIG_COLLECTION,
    LAST_FETCH_STATE_DOC_ID,
    LAST_PROCESSED_TS_FIELD
)

logger = logging.getLogger(__name__) # Używamy loggera zdefiniowanego w main

logger.info("[FETCHER] Moduł fetch_from_firestore.py załadowany.")

def load_last_processed_timestamp() -> str:
    db = get_db() # Pobierz klienta Firestore
    # ... (reszta funkcji bez zmian, używając lokalnej zmiennej 'db') ...
    try:
        doc_ref = db.collection(BOT_CONFIG_COLLECTION).document(LAST_FETCH_STATE_DOC_ID)
        doc = doc_ref.get()
        # ... itd. ...
    except Exception as e:
        logger.error(f"[FETCHER_ERROR] Nie udało się odczytać timestampa z Firestore: {e}", exc_info=True) # Lepsze logowanie błędu
        return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat() # Domyślna wartość
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat() # Upewnij się, że zawsze coś zwracasz


def save_last_processed_timestamp(timestamp_str: str):
    db = get_db() # Pobierz klienta Firestore
    # ... (reszta funkcji bez zmian, używając lokalnej zmiennej 'db') ...
    try:
        ts_dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        # ... itd. ...
        doc_ref = db.collection(BOT_CONFIG_COLLECTION).document(LAST_FETCH_STATE_DOC_ID)
        doc_ref.set({LAST_PROCESSED_TS_FIELD: ts_dt}, merge=True)
    except Exception as e:
        logger.error(f"[FETCHER_ERROR] Nie udało się zapisać timestampu {timestamp_str} do Firestore: {e}", exc_info=True)


def fetch_new_alerts_since(last_ts_str: str):
    db = get_db() # Pobierz klienta Firestore
    # ... (reszta funkcji bez zmian, używając lokalnej zmiennej 'db') ...
    new_alerts_list = []
    # ... itd. ...
    try:
        # ... (logika pobierania) ...
        pass
    except firebase_admin.exceptions.FirebaseError as fb_err:
        logger.error(f"[FETCHER_FIRESTORE_ERROR] Błąd Firebase: {fb_err}", exc_info=True)
    except Exception as e:
        logger.error(f"[FETCHER_FIRESTORE_ERROR] Inny błąd podczas pobierania: {e}", exc_info=True)
    
    # Upewnij się, że zawsze zwracasz krotkę
    return new_alerts_list, datetime.fromisoformat(last_ts_str.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).isoformat()


def fetcher_loop(): # Ta funkcja będzie teraz używać get_db()
    logger.info("[FETCHER] Rozpoczynam pętlę fetcher_loop().")
    # Sprawdzenie, czy Firebase jest dostępne, odbywa się teraz w get_db()
    # Jeśli get_db() rzuci wyjątkiem, pętla się nie uruchomi, co jest OK.
    
    # Aby uniknąć błędu z state_manager (jeśli jeszcze nie jest w pełni zintegrowany),
    # zakomentujmy na razie logikę pętli
    # current_last_processed_ts = load_last_processed_timestamp()
    # logger.info(f"[FETCHER] Pętla fetchera uruchomiona. Początkowy timestamp: {current_last_processed_ts}")
    # while True:
    #     try:
    #         # ... logika ...
    #         # state_manager.process_alert(alert_data) 
    #         pass
    #     except Exception as e:
    #         logger.error(f"[FETCHER_ERROR] Nieoczekiwany błąd w pętli fetchera: {e}", exc_info=True)
    #     time.sleep(FETCH_INTERVAL_SECONDS)
    logger.info("[FETCHER] Pętla fetcher_loop() zakończona (tymczasowo pusta dla testu).")
    pass

logger.info("[FETCHER] Definicje funkcji w fetch_from_firestore.py ZAKOŃCZONE.")