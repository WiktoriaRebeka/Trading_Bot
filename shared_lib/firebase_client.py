#trading_bot/shared_lib/firebase_client.py


import os
from google.cloud import firestore
import logging

# Używamy __name__, aby logger automatycznie przyjął nazwę modułu: 'firebase_client'
logger = logging.getLogger(__name__)
db_client = None

def initialize_firebase():
    global db_client

    if db_client is not None:
        logger.debug("Klient Firestore jest już zainicjalizowany.")
        return True

    try:
        # W środowisku GCP projekt jest zwykle wykrywany automatycznie.
        project_id = os.getenv("GCP_PROJECT")
        database_name = "trading-bot-data"
        logger.info(f"Inicjalizacja klienta Firestore dla projektu '{project_id}' i bazy '{database_name}'...")
        
        # Używamy standardowego, publicznego sposobu inicjalizacji klienta
        db_client = firestore.Client(database=database_name)
        
        # Proste zapytanie weryfikujące połączenie
        db_client.collection('_test_connection_').limit(1).get()
        
        logger.info("Inicjalizacja Firestore zakończona sukcesem.")
        return True
    
    except Exception as e:
        logger.critical(f"KRYTYCZNY BŁĄD: Inicjalizacja klienta Firestore nie powiodła się: {e}", exc_info=True)
        db_client = None
        return False

def get_db():
    """Zwraca zainicjalizowanego klienta Firestore lub zgłasza wyjątek."""
    if db_client is None:
        logger.critical("Próba użycia niezainicjalizowanego klienta Firestore.")
        raise Exception("Krytyczny błąd: Klient Firestore nie został pomyślnie zainicjalizowany.")
    return db_client