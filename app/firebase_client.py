import os
import firebase_admin
from firebase_admin import firestore
import logging

logger = logging.getLogger(__name__)
db_client = None

def initialize_firebase():
    global db_client
    if not firebase_admin._apps:
        try:
            logger.info("Inicjalizacja Firebase Admin SDK dla App Engine...")
            
            # W środowisku Google Cloud nie potrzebujemy pliku z kluczem,
            # uprawnienia są dziedziczone z konta usługi App Engine.
            # Musimy jednak jawnie podać ID projektu, aby uniknąć niejasności.
            project_id = os.getenv('GCP_PROJECT_ID')
            if not project_id:
                raise ValueError("Zmienna środowiskowa GCP_PROJECT_ID nie jest ustawiona w app.yaml.")

            firebase_admin.initialize_app(options={
                'projectId': project_id
            })
            
            # Tworzymy klienta, wskazując na konkretną, nazwaną bazę danych.
            # Jeśli byśmy tego nie zrobili, próbowałby się połączyć z nieistniejącą bazą '(default)'.
            db_client = firestore.client(database="trading-bot-data")
            
            logger.info(f"Inicjalizacja Firebase i połączenie z bazą 'trading-bot-data' w projekcie '{project_id}' zakończone sukcesem.")
            return True
            
        except Exception as e:
            logger.error(f"Krytyczny błąd inicjalizacji Firebase: {e}", exc_info=True)
            return False
    else:
        # Jeśli już zainicjowano, upewnij się, że klient jest ustawiony
        if not db_client:
            db_client = firestore.client(database="trading-bot-data")
        logger.info("Firebase Admin SDK już zainicjowane.")
        return True

def get_db():
    if db_client is None:
        # Ten błąd może się pojawić, jeśli initialize_firebase() nie powiodło się
        raise Exception("Klient Firestore nie został zainicjowany. Wywołaj initialize_firebase() najpierw.")
    return db_client