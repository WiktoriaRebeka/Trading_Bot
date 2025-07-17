# Lokalizacja: shared_lib/firebase_client.py 

import os
import logging
from typing import List

from google.cloud import firestore
from google.api_core.client_options import ClientOptions

from shared_lib import constants

logger = logging.getLogger(__name__)
db_client = None

def initialize_firebase() -> bool:
    """Inicjalizuje globalnego klienta Firestore, jawnie określając ID projektu i bazy danych."""
    global db_client

    if db_client is not None:
        return True

    try:
        project_id = os.getenv("GCP_PROJECT", "trading-bot-463318")
        
        # --- KLUCZOWA ZMIANA: JAWNIE PODAJEMY NAZWĘ BAZY DANYCH ---
        database_id = "trading-bot-data"
        
        logger.info(f"Inicjalizacja klienta Firestore dla projektu '{project_id}' i bazy '{database_id}'...")

        # Przekazujemy ID bazy danych do konstruktora klienta
        db_client = firestore.Client(
            project=project_id,
            database=database_id
        )

        # Szybki test, czy możemy połączyć się z bazą i odczytać dane
        # Ten test teraz powinien się powieść
        test_doc_ref = db_client.collection(constants.BOT_CONFIG_COLLECTION).document(constants.SYMBOLS_CONFIG_DOC_ID)
        test_doc = test_doc_ref.get()
        if not test_doc.exists:
            # Jeśli to się nie uda, to znaczy, że problem jest jeszcze gdzieś indziej, ale to mało prawdopodobne
            logger.warning("Testowy odczyt dokumentu konfiguracji nie powiódł się. Sprawdź, czy dokument na pewno istnieje w bazie 'trading-bot-data'.")
        
        logger.info(f"Inicjalizacja Firestore dla bazy '{database_id}' zakończona sukcesem.")
        return True
    
    except Exception as e:
        logger.critical(f"KRYTYCZNY BŁĄD podczas inicjalizacji Firestore: {e}", exc_info=True)
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
                logger.info(f"Pobrano {len(symbols)} symboli do monitorowania z konfiguracji.")
                return symbols
            else:
                logger.error("Pole 'symbols_to_watch' w konfiguracji nie jest listą.")
        else:
            logger.warning(f"Dokument konfiguracyjny '{constants.SYMBOLS_CONFIG_DOC_ID}' nie istnieje w bazie danych '{db.database}'.")
    except Exception as e:
        logger.error(f"Błąd podczas pobierania konfiguracji symboli: {e}", exc_info=True)
    return []