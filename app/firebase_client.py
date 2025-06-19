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
            
            project_id = os.getenv('GCP_PROJECT_ID')
            if not project_id:
                raise ValueError("Zmienna środowiskowa GCP_PROJECT_ID nie jest ustawiona w app.yaml.")

            # W środowisku GAE, wystarczy podać ID projektu.
            # SDK automatycznie użyje domyślnych uprawnień.
            firebase_admin.initialize_app(options={
                'projectId': project_id
            })
            
            # Klient połączy się z jedyną dostępną bazą w tym projekcie.
            db_client = firestore.client()
            
            logger.info(f"Inicjalizacja Firebase w projekcie '{project_id}' zakończona sukcesem.")
            return True
            
        except Exception as e:
            logger.error(f"Krytyczny błąd inicjalizacji Firebase: {e}", exc_info=True)
            return False
    else:
        if not db_client:
            db_client = firestore.client()
        logger.info("Firebase Admin SDK już zainicjowane.")
        return True

def get_db():
    if db_client is None:
        raise Exception("Klient Firestore nie został zainicjowany. Wywołaj initialize_firebase() najpierw.")
    return db_client