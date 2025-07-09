#Lokalizacja: shared_lib/firebase_client.py


import os
from google.cloud import firestore
import logging

logger = logging.getLogger(__name__)
db_client = None
def initialize_firebase() -> bool:
    """Inicjalizuje globalnego klienta Firestore. Zwraca True w przypadku sukcesu."""
    global db_client

    if db_client is not None:
        logger.debug("Klient Firestore jest już zainicjalizowany.")
        return True

    try:
        project_id = os.getenv("GCP_PROJECT")

        database_id = "trading-bot-data"
        
        logger.info(f"Inicjalizacja klienta Firestore dla projektu '{project_id}' i bazy '{database_id}'...")

        db_client = firestore.Client(project=project_id, database=database_id)

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