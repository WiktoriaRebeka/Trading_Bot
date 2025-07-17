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
    global db_client

    if db_client is not None:
        return True

    try:
        project_id = os.getenv("GCP_PROJECT", "trading-bot-463318")
        database_id = "trading-bot-data"
        api_endpoint = f"{FIRESTORE_REGION}-firestore.googleapis.com"
        client_options = ClientOptions(api_endpoint=api_endpoint)
        
        logger.info(f"DIAGNOSTYKA: Inicjalizacja klienta Firestore dla projektu '{project_id}' w regionie '{FIRESTORE_REGION}'...")

        db_client = firestore.Client(
            project=project_id,
            database=database_id,
            client_options=client_options
        )
        

        collections = [c.id for c in db_client.collections()]
        logger.info(f"DIAGNOSTYKA: Znalezione kolekcje w bazie: {collections}")
        
        logger.info(f"DIAGNOSTYKA: Inicjalizacja Firestore zakończona sukcesem.")
        return True
    
    except Exception as e:

        logger.critical(f"DIAGNOSTYKA: KRYTYCZNY BŁĄD podczas inicjalizacji Firestore: Typ błędu: {type(e).__name__}, Treść: {e}", exc_info=True)

        db_client = None
        return False

def get_db() -> firestore.Client:
    if db_client is None:
        raise RuntimeError("Krytyczny błąd: Klient Firestore nie został pomyślnie zainicjalizowany.")
    return db_client

def get_symbols_to_watch_from_config() -> List[str]:
    logger.info("DIAGNOSTYKA: Wejście do funkcji get_symbols_to_watch_from_config.")
    try:
        db = get_db()
        collection_name = constants.BOT_CONFIG_COLLECTION
        doc_id = constants.SYMBOLS_CONFIG_DOC_ID
        
        # --- LOGOWANIE DEBUGOWE ---
        logger.info(f"DIAGNOSTYKA: Próba odczytu dokumentu: kolekcja='{collection_name}', dokument='{doc_id}'.")
        doc_ref = db.collection(collection_name).document(doc_id)
        doc = doc_ref.get()
        logger.info(f"DIAGNOSTYKA: Operacja .get() zakończona.")

        if doc.exists:
            logger.info("DIAGNOSTYKA: doc.exists zwróciło TRUE.")
            doc_data = doc.to_dict()
            logger.info(f"DIAGNOSTYKA: Zawartość dokumentu (to_dict()): {doc_data}")
            symbols = doc_data.get("symbols_to_watch", [])
            logger.info(f"DIAGNOSTYKA: Odczytano pole 'symbols_to_watch', wynik: {symbols}")
            
            if isinstance(symbols, list):
                logger.info(f"Pobrano {len(symbols)} symboli do monitorowania z konfiguracji.")
                return symbols
            else:
                logger.error("DIAGNOSTYKA: Pole 'symbols_to_watch' nie jest listą.")
        else:
            # To jest blok, który prawdopodobnie jest wykonywany
            logger.warning("DIAGNOSTYKA: doc.exists zwróciło FALSE. Dokument nie został znaleziony przez bibliotekę.")
            
    except Exception as e:
        # --- LOGOWANIE DEBUGOWE ---
        logger.error(f"DIAGNOSTYKA: Błąd wewnątrz get_symbols_to_watch_from_config: Typ błędu: {type(e).__name__}, Treść: {e}", exc_info=True)
        # --- KONIEC LOGOWANIA DEBUGOWEGO ---
    
    logger.warning("DIAGNOSTYKA: Funkcja kończy działanie i zwraca PUSTĄ listę.")
    return []