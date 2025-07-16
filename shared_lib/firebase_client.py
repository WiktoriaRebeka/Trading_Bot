# Lokalizacja: shared_lib/firebase_client.py

import logging
from typing import Optional, List
from google.cloud import firestore
from google.cloud.firestore_v1.client import Client



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
            project="trading-bot-463318", # Hardkodujemy, aby mieć pewność
            database="trading-bot-data"
        )
        # Test połączenia
        db_client.collection("bot_config").limit(1).get()
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
        # --- KLUCZOWA ZMIANA: Hardkodujemy nazwy, aby uniknąć problemów z importem ---
        collection_name = "bot_config"
        document_id = "symbols_config"
        field_name = "symbols_to_watch"
        
        doc_ref = db.collection(collection_name).document(document_id)
        doc = doc_ref.get()
        
        if doc.exists:
            symbols = doc.to_dict().get(field_name, [])
            if isinstance(symbols, list):
                logger.info(f"Pobrano {len(symbols)} symboli do monitorowania z '{collection_name}/{document_id}'.")
                return symbols
            else:
                logger.error(f"Pole '{field_name}' w konfiguracji nie jest listą.")
        else:
            logger.warning(f"Dokument konfiguracyjny '{collection_name}/{document_id}' nie istnieje.")
    except Exception as e:
        logger.error(f"Błąd podczas pobierania konfiguracji symboli: {e}", exc_info=True)
    return []