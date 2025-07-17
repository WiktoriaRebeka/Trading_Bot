# Lokalizacja: shared_lib/firebase_client.py

import os
from google.cloud import firestore
import logging
from typing import List

# Importujemy stałe, tak jak to było w starej wersji
from shared_lib import constants

logger = logging.getLogger(__name__)
db_client = None

def initialize_firebase() -> bool:
    """Inicjalizuje globalnego klienta Firestore. Zwraca True w przypadku sukcesu."""
    global db_client

    if db_client is not None:
        logger.debug("Klient Firestore jest już zainicjalizowany.")
        return True

    try:
        # Wracamy do polegania na zmiennej środowiskowej, którą ustawiamy w cloudbuild.yaml
        project_id = os.getenv("GCP_PROJECT")
        if not project_id:
            logger.critical("Zmienna środowiskowa GCP_PROJECT nie jest ustawiona!")
            # Używamy domyślnej jako zabezpieczenie
            project_id = "trading-bot-463318"

        database_id = "trading-bot-data"
        
        logger.info(f"Inicjalizacja klienta Firestore dla projektu '{project_id}' i bazy '{database_id}'...")

        db_client = firestore.Client(project=project_id, database=database_id)

        # Test połączenia
        db_client.collection('_test_connection_').limit(1).get()
        
        logger.info("Inicjalizacja Firestore zakończona sukcesem.")
        return True
    
    except Exception as e:
        logger.critical(f"KRYTYCZNY BŁĄD: Inicjalizacja klienta Firestore nie powiodła się: {e}", exc_info=True)
        db_client = None
        return False

def get_db() -> firestore.Client:
    """Zwraca zainicjalizowanego klienta Firestore lub zgłasza wyjątek, jeśli inicjalizacja się nie powiodła."""
    if db_client is None:
        raise RuntimeError("Krytyczny błąd: Klient Firestore nie został pomyślnie zainicjalizowany.")
    return db_client

# --- Dodajemy funkcję, której potrzebuje data_collector, używając starej logiki ---
def get_symbols_to_watch_from_config() -> List[str]:
    """Pobiera listę symboli do monitorowania z dokumentu konfiguracyjnego w Firestore."""
    try:
        db = get_db()
        doc_ref = db.collection(constants.BOT_CONFIG_COLLECTION).document(constants.SYMBOLS_CONFIG_DOC_ID)
        doc = doc_ref.get()
        
        if doc.exists:
            # Wracamy do "wypalonej" nazwy pola, aby mieć pewność
            symbols = doc.to_dict().get("symbols_to_watch", [])
            if isinstance(symbols, list):
                logger.info(f"Pobrano {len(symbols)} symboli do monitorowania z konfiguracji.")
                return symbols
            else:
                logger.error("Pole 'symbols_to_watch' w konfiguracji nie jest listą.")
        else:
            logger.warning(f"Dokument konfiguracyjny '{constants.SYMBOLS_CONFIG_DOC_ID}' nie istnieje.")
    except Exception as e:
        logger.error(f"Błąd podczas pobierania konfiguracji symboli: {e}", exc_info=True)
    return []