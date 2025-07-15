# Lokalizacja: shared_lib/firebase_client.py

import logging
from typing import Optional, List
import firebase_admin
from firebase_admin import firestore
from google.cloud.firestore_v1.client import Client

from shared_lib import constants

logger = logging.getLogger(__name__)

db_client: Optional[Client] = None
_app_initialized = False

def initialize_firebase() -> bool:
    """
    Inicjalizuje połączenie z Firebase Admin SDK, wskazując na konkretną bazę danych.
    """
    global db_client, _app_initialized
    
    if _app_initialized:
        logger.debug("Firebase jest już zainicjalizowany.")
        return True

    try:
        logger.info("Inicjalizuję Firebase Admin SDK...")
        
        if not firebase_admin._apps:
            firebase_admin.initialize_app(options={
                'projectId': constants.GCP_PROJECT_ID,
            })
        
        # --- OSTATECZNA, KLUCZOWA ZMIANA: Podajemy prawidłowe ID bazy danych ---
        database_id = "trading-bot-data" 
        db_client = firestore.client(database=database_id)
        
        # Teraz, gdy wskazujemy na właściwą bazę, możemy przywrócić krok weryfikacji.
        logger.info(f"Sprawdzam połączenie z bazą danych Firestore: '{database_id}'...")
        db_client.collection(constants.BOT_CONFIG_COLLECTION).limit(1).get()
        logger.info("Weryfikacja połączenia z Firestore pomyślna.")
        
        _app_initialized = True
        logger.info(f"SUKCES! Klient Firestore dla bazy '{database_id}' został pomyślnie utworzony i zweryfikowany.")
        return True
    except Exception as e:
        logger.critical(f"KRYTYCZNY BŁĄD: Nie udało się zainicjalizować klienta Firestore: {e}", exc_info=True)
        db_client = None
        _app_initialized = False
        return False

def get_db() -> Client:
    """
    Zwraca zainicjalizowanego klienta Firestore.
    """
    if db_client is None or not _app_initialized:
        raise RuntimeError("Krytyczny błąd: Klient Firestore nie został pomyślnie zainicjalizowany.")
    return db_client

def get_symbols_to_watch_from_config() -> List[str]:
    """Pobiera listę symboli do monitorowania z dokumentu konfiguracyjnego w Firestore."""
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