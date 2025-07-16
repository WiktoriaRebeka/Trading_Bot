# Lokalizacja: shared_lib/firebase_client.py

import logging
from typing import Optional, List

# --- KLUCZOWA ZMIANA: Importujemy tylko i wyłącznie klienta google-cloud-firestore ---
from google.cloud import firestore
from google.cloud.firestore_v1.client import Client

from shared_lib import constants

logger = logging.getLogger(__name__)

db_client: Optional[Client] = None
_client_initialized = False

def initialize_firebase() -> bool:
    """
    Inicjalizuje klienta Firestore bezpośrednio, używając biblioteki google-cloud-firestore.
    """
    global db_client, _client_initialized
    
    if _client_initialized:
        logger.debug("Klient Firestore jest już zainicjalizowany.")
        return True

    try:
        logger.info("Inicjalizuję klienta Google Cloud Firestore...")
        
        # --- OSTATECZNA, POPRAWNA SKŁADNIA ---
        # Tworzymy klienta bezpośrednio z google.cloud.firestore.Client,
        # podając ID projektu i ID bazy danych.
        db_client = firestore.Client(
            project=constants.GCP_PROJECT_ID,
            database="trading-bot-data"
        )
        
        logger.info("Sprawdzam połączenie z bazą danych Firestore: 'trading-bot-data'...")
        db_client.collection(constants.BOT_CONFIG_COLLECTION).limit(1).get()
        logger.info("Weryfikacja połączenia z Firestore pomyślna.")
        
        _client_initialized = True
        logger.info("SUKCES! Klient Firestore został pomyślnie utworzony i zweryfikowany.")
        return True
    except Exception as e:
        logger.critical(f"KRYTYCZNY BŁĄD: Nie udało się zainicjalizować klienta Firestore: {e}", exc_info=True)
        db_client = None
        _client_initialized = False
        return False

def get_db() -> Client:
    """Zwraca zainicjalizowanego klienta Firestore."""
    if db_client is None or not _client_initialized:
        raise RuntimeError("Krytyczny błąd: Klient Firestore nie został pomyślnie zainicjalizowany.")
    return db_client

# Reszta pliku (get_symbols_to_watch_from_config) pozostaje bez zmian
def get_symbols_to_watch_from_config() -> List[str]:
    try:
        db = get_db()
        doc_ref = db.collection(constants.BOT_CONFIG_COLLECTION).document(constants.SYMBOLS_CONFIG_DOC_ID)
        doc = doc_ref.get()
        if doc.exists:
            symbols = doc.to_dict().get("symbols", [])
            if isinstance(symbols, list):
                logger.info(f"Pobrano {len(symbols)} symboli do monitorowania z konfiguracji.")
                return symbols
            else:
                logger.error("Pole 'symbols' w konfiguracji nie jest listą.")
        else:
            logger.warning("Dokument konfiguracyjny symboli nie istnieje.")
    except Exception as e:
        logger.error(f"Błąd podczas pobierania konfiguracji symboli: {e}", exc_info=True)
    return []