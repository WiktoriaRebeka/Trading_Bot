#trading_bot/firebase_client.py

import os
from google.cloud import firestore
import logging

logger = logging.getLogger(__name__)
db_client = None

def initialize_firebase():
    global db_client

    if db_client is not None:
        logger.info("Klient Firestore jest już zainicjalizowany.")
        return True

    try:
        database_name = "trading-bot-data"
        logger.info(f"Inicjalizacja klienta Firestore dla domyślnego projektu GCP i bazy '{database_name}'...")
        
        # NAJWAŻNIEJSZA ZMIANA: Usuwamy jawne podawanie `project`.
        # To jest "tryb Google Cloud".
        db_client = firestore.Client(database=database_name)
        
        # Testowe zapytanie, aby upewnić się, że połączenie działa
        db_client.collection('_test_connection_').limit(1).get()
        
        logger.info("Inicjalizacja Firestore ZAKOŃCZONA SUKCESEM.")
        return True
        
    except Exception as e:
        logger.critical(f"KRYTYCZNY BŁĄD podczas inicjalizacji Firestore w App Engine: {e}", exc_info=True)
        db_client = None
        return False

def get_db():
    """Zwraca zainicjalizowanego klienta Firestore."""
    if db_client is None:
        raise Exception("Krytyczny błąd: Próba użycia klienta Firestore, który nie został pomyślnie zainicjalizowany.")
    return db_client