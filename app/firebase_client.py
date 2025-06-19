import os
import firebase_admin
from firebase_admin import firestore
import logging

logger = logging.getLogger(__name__)
db_client = None

def initialize_firebase():
    """
    Inicjalizuje połączenie z Firebase Admin SDK.
    W środowisku App Engine SDK automatycznie używa uprawnień konta usługi App Engine.
    """
    global db_client
    
    # Używamy idempotentności - inicjalizujemy tylko raz.
    if not firebase_admin._apps:
        try:
            logger.info("Inicjalizacja Firebase Admin SDK dla środowiska App Engine...")
            
            # W środowisku Google Cloud, GCP_PROJECT_ID jest zwykle ustawione automatycznie.
            # My ustawiamy je jawnie w app.yaml dla pewności.
            project_id = os.getenv('GCP_PROJECT_ID')
            database_name = "trading-bot-data" # <-- WAŻNE: Nazwa Twojej bazy

            if not project_id:
                raise ValueError("Zmienna środowiskowa GCP_PROJECT_ID nie jest ustawiona.")

            # Dla App Engine nie potrzebujemy pliku klucza. Wystarczy ID projektu.
            firebase_admin.initialize_app(options={'projectId': project_id})
            
            # Tworzymy klienta do NAZWANEJ bazy danych i przechowujemy go.
            db_client = firestore.client(database=database_name)
            
            logger.info(f"Inicjalizacja Firebase w projekcie '{project_id}' zakończona. Połączono z bazą '{database_name}'.")
            return True
            
        except Exception as e:
            logger.critical(f"KRYTYCZNY BŁĄD inicjalizacji Firebase: {e}", exc_info=True)
            return False
    else:
        # Jeśli aplikacja jest już zainicjalizowana, upewnij się, że klient DB istnieje
        if db_client is None:
            database_name = "trading-bot-data"
            db_client = firestore.client(database=database_name)
        logger.info("Firebase Admin SDK już zainicjowane.")
        return True

def get_db():
    """
    Zwraca zainicjalizowanego klienta Firestore.
    Wywołaj initialize_firebase() przed pierwszym użyciem tej funkcji.
    """
    if db_client is None:
        # To nie powinno się zdarzyć, jeśli initialize_firebase() jest wywołane na starcie aplikacji.
        logger.error("Próba pobrania klienta Firestore przed inicjalizacją!")
        raise Exception("Klient Firestore nie został zainicjalizowany. Wywołaj initialize_firebase() na początku działania aplikacji.")
    return db_client