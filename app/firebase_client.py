import os
from google.cloud import firestore
import logging

logger = logging.getLogger(__name__)
db_client = None

def initialize_firebase():
    """
    Inicjalizuje połączenie z Firestore używając bezpośredniej, jawnej metody.
    """
    global db_client

    if db_client is not None:
        logger.info("Klient Firestore jest już zainicjalizowany.")
        return True

    try:
        # Pobieramy ID projektu ze zmiennej środowiskowej ustawionej w app.yaml
        project_id = os.getenv('GCP_PROJECT_ID')
        database_name = "trading-bot-data"

        if not project_id:
            raise ValueError("Krytyczna zmienna środowiskowa GCP_PROJECT_ID nie jest ustawiona w app.yaml.")
        
        logger.info(f"Inicjalizacja klienta Firestore dla projektu '{project_id}' i bazy '{database_name}'...")
        
        # NAJWAŻNIEJSZE: Używamy tej samej, sprawdzonej metody co w webhooku.
        db_client = firestore.Client(project=project_id, database=database_name)
        
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
        raise Exception("Próba pobrania klienta Firestore przed udaną inicjalizacją.")
    return db_client