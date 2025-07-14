# Lokalizacja: shared_lib/firebase_client.py

import logging
from typing import Optional, List, Set
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.client import Client

from shared_lib import constants

logger = logging.getLogger(__name__)

db_client: Optional[Client] = None
_app_initialized = False

def initialize_firebase() -> bool:
    """
    Inicjalizuje połączenie z Firebase Admin SDK.
    Używa wzorca singleton, aby zapewnić, że inicjalizacja nastąpi tylko raz.
    Zwraca True, jeśli inicjalizacja się powiodła lub już została wykonana.
    """
    global db_client, _app_initialized
    
    # Jeśli już zainicjalizowano pomyślnie, nie rób nic i zwróć sukces.
    if _app_initialized:
        logger.debug("Firebase jest już zainicjalizowany.")
        return True

    try:
        # Ten blok zostanie wykonany tylko przy pierwszym wywołaniu.
        logger.info("Inicjalizuję Firebase Admin SDK...")
        # W środowisku GCP (Cloud Run, Cloud Functions) nie trzeba podawać credentials.
        # `firebase_admin` automatycznie użyje uprawnień konta serwisowego.
        # To jest najbezpieczniejsza i zalecana metoda.
        firebase_admin.initialize_app(options={
            'projectId': constants.GCP_PROJECT_ID,
        })
        db_client = firestore.client()
        
        # Sprawdzenie połączenia poprzez próbę odczytu dokumentu (opcjonalne, ale dobre)
        db_client.collection(constants.BOT_CONFIG_COLLECTION).limit(1).get()
        
        _app_initialized = True
        logger.info("SUKCES! Połączenie z Firebase (Firestore) zostało pomyślnie nawiązane.")
        return True
    except Exception as e:
        logger.critical(f"KRYTYCZNY BŁĄD: Nie udało się zainicjalizować Firebase Admin SDK: {e}", exc_info=True)
        db_client = None
        _app_initialized = False
        return False

def get_db() -> Client:
    """
    Zwraca zainicjalizowanego klienta Firestore.
    Rzuca wyjątek, jeśli inicjalizacja nie została pomyślnie przeprowadzona.
    """
    if db_client is None or not _app_initialized:
        raise RuntimeError("Krytyczny błąd: Klient Firestore nie został pomyślnie zainicjalizowany. Wywołaj initialize_firebase() na starcie aplikacji.")
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