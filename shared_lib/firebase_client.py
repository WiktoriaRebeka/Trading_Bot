# Lokalizacja: shared_lib/firebase_client.py

import logging
from typing import Optional, List
from google.cloud import firestore
from google.cloud.firestore_v1.client import Client

# Przywracamy import, teraz jest bezpieczny
from shared_lib import constants

logger = logging.getLogger(__name__)

db_client: Optional[Client] = None
_client_initialized = False

def initialize_firebase() -> bool:
    global db_client, _client_initialized
    if _client_initialized:
        return True
    try:
        logger.info("Inicjalizuję klienta Google Cloud Firestore...")
        db_client = firestore.Client(
            project=constants.GCP_PROJECT_ID,
            database=constants.FIRESTORE_DATABASE_ID
        )
        # Test połączenia
        db_client.collection(constants.BOT_CONFIG_COLLECTION).limit(1).get()
        _client_initialized = True
        logger.info("SUKCES! Klient Firestore został pomyślnie utworzony i zweryfikowany.")
        return True
    except Exception as e:
        logger.critical(f"KRYTYCZNY BŁĄD: Nie udało się zainicjalizować klienta Firestore: {e}", exc_info=True)
        db_client = None
        _client_initialized = False
        return False

def get_db() -> Client:
    if db_client is None or not _client_initialized:
        raise RuntimeError("Krytyczny błąd: Klient Firestore nie został pomyślnie zainicjalizowany.")
    return db_client

def get_symbols_to_watch_from_config() -> List[str]:
    """Pobiera listę symboli do monitorowania z dokumentu konfiguracyjnego w Firestore."""
    try:
        db = get_db()
        doc_ref = db.collection(constants.BOT_CONFIG_COLLECTION).document(constants.SYMBOLS_CONFIG_DOC_ID)
        doc = doc_ref.get()
        
        if doc.exists:
            # Używamy teraz stałej dla nazwy pola
            symbols = doc.to_dict().get(constants.SYMBOLS_FIELD_NAME, [])
            if isinstance(symbols, list):
                logger.info(f"Pobrano {len(symbols)} symboli do monitorowania z konfiguracji.")
                return symbols
            else:
                logger.error(f"Pole '{constants.SYMBOLS_FIELD_NAME}' w konfiguracji nie jest listą.")
        else:
            logger.warning(f"Dokument konfiguracyjny '{constants.SYMBOLS_CONFIG_DOC_ID}' nie istnieje.")
    except Exception as e:
        logger.error(f"Błąd podczas pobierania konfiguracji symboli: {e}", exc_info=True)
    return []