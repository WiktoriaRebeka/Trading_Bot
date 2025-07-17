# Lokalizacja: shared_lib/firebase_client.py 

import os
import logging
from typing import List

from google.cloud import firestore
from google.api_core.client_options import ClientOptions 

from shared_lib import constants

logger = logging.getLogger(__name__)
db_client = None


FIRESTORE_REGION = "us-central1"

def initialize_firebase() -> bool:
    """Inicjalizuje globalnego klienta Firestore, jawnie określając jego lokalizację."""
    global db_client

    if db_client is not None:
        logger.debug("Klient Firestore jest już zainicjalizowany.")
        return True

    try:
        project_id = os.getenv("GCP_PROJECT", "trading-bot-463318")
        database_id = "trading-bot-data"
        
        api_endpoint = f"{FIRESTORE_REGION}-firestore.googleapis.com"
        client_options = ClientOptions(api_endpoint=api_endpoint)
        
        logger.info(f"Inicjalizacja klienta Firestore dla projektu '{project_id}', bazy '{database_id}' w regionie '{FIRESTORE_REGION}' (nam5)...")

        db_client = firestore.Client(
            project=project_id,
            database=database_id,
            client_options=client_options
        )

        db_client.collection('_test_connection_').limit(1).get()
        
        logger.info(f"Inicjalizacja Firestore zakończona sukcesem. Połączono z regionem {FIRESTORE_REGION} (nam5).")
        return True
    
    except Exception as e:
        logger.critical(f"KRYTYCZNY BŁĄD: Inicjalizacja klienta Firestore nie powiodła się: {e}", exc_info=True)
        db_client = None
        return False

def get_db() -> firestore.Client:
    """Zwraca zainicjalizowanego klienta Firestore lub zgłasza wyjątek."""
    if db_client is None:
        raise RuntimeError("Krytyczny błąd: Klient Firestore nie został pomyślnie zainicjalizowany.")
    return db_client

def get_symbols_to_watch_from_config() -> List[str]:
    """Pobiera listę symboli do monitorowania z dokumentu konfiguracyjnego w Firestore."""
    try:
        db = get_db()
        doc_ref = db.collection(constants.BOT_CONFIG_COLLECTION).document(constants.SYMBOLS_CONFIG_DOC_ID)
        doc = doc_ref.get()
        
        if doc.exists:
            symbols = doc.to_dict().get("symbols_to_watch", [])
            if isinstance(symbols, list):
                logger.info(f"Pobrano {len(symbols)} symboli do monitorowania z konfiguracji w {FIRESTORE_REGION}.")
                return symbols
            else:
                logger.error("Pole 'symbols_to_watch' w konfiguracji nie jest listą.")
        else:
            logger.warning(f"Dokument konfiguracyjny '{constants.SYMBOLS_CONFIG_DOC_ID}' nie istnieje w bazie danych.")
    except Exception as e:
        logger.error(f"Błąd podczas pobierania konfiguracji symboli: {e}", exc_info=True)
    return []