# app/firebase_client.py
import firebase_admin
from firebase_admin import firestore, credentials
import logging

logger = logging.getLogger(__name__)
db_client = None

def initialize_firebase():
    global db_client
    if not firebase_admin._apps:
        try:
            logger.info("Inicjalizacja Firebase Admin SDK...")
            # W środowisku Google Cloud nie potrzebujesz pliku credentials.json
            firebase_admin.initialize_app()
            db_client = firestore.client()
            logger.info("Inicjalizacja Firebase i połączenie z Firestore zakończone sukcesem.")
            return True
        except Exception as e:
            logger.error(f"Krytyczny błąd inicjalizacji Firebase: {e}", exc_info=True)
            return False
    else:
        db_client = firestore.client()
        logger.info("Firebase Admin SDK już zainicjowane.")
        return True

def get_db():
    if db_client is None:
        raise Exception("Klient Firestore nie został zainicjowany. Wywołaj initialize_firebase() najpierw.")
    return db_client