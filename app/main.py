# TRADING_BOT/app/main.py
from flask import Flask
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
import logging
import os
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info(f"[FIREBASE_TEST_APP] START - Wersja testowa Firebase Init 1.0. Czas: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
logger.info(f"[FIREBASE_TEST_APP] GAE_ENV: {os.getenv('GAE_ENV')}")
logger.info(f"[FIREBASE_TEST_APP] GOOGLE_APPLICATION_CREDENTIALS: {os.getenv('GOOGLE_APPLICATION_CREDENTIALS')}")

flask_app = Flask(__name__)

# Inicjalizacja Firebase Admin SDK
# W App Engine Standard, jeśli nie podasz credentials, powinien użyć domyślnych poświadczeń środowiska.
# Jeśli masz plik klucza serwisowego i chcesz go użyć (np. lokalnie), musisz go dostarczyć.
# Dla testu w App Engine, spróbujmy bez explicit credentials.
try:
    logger.info("[FIREBASE_TEST_APP] Próba inicjalizacji firebase_admin...")
    # Jeśli PROJECT_ID jest ustawiony jako zmienna środowiskowa w app.yaml, można go użyć:
    # project_id = os.getenv('GCP_PROJECT') # lub FIREBASE_PROJECT_ID, jeśli taką masz
    # if project_id:
    #    firebase_admin.initialize_app(options={'projectId': project_id})
    # else:
    #    firebase_admin.initialize_app() # Użyje domyślnych poświadczeń App Engine

    # Dla pewności, że użyje domyślnych poświadczeń, spróbuj bez opcji:
    if not firebase_admin._apps: # Sprawdź, czy aplikacja nie została już zainicjowana
         firebase_admin.initialize_app()
    logger.info("[FIREBASE_TEST_APP] Inicjalizacja firebase_admin ZAKOŃCZONA SUKCESEM.")

    logger.info("[FIREBASE_TEST_APP] Próba połączenia z Firestore...")
    db = firestore.client()
    logger.info("[FIREBASE_TEST_APP] Połączenie z Firestore ZAKOŃCZONE SUKCESEM.")

    logger.info("[FIREBASE_TEST_APP] Próba odczytu dokumentu z Firestore (bot_config/last_fetch_state)...")
    doc_ref = db.collection('bot_config').document('last_fetch_state')
    doc = doc_ref.get()
    if doc.exists:
        logger.info(f"[FIREBASE_TEST_APP] Odczytano dokument: {doc.to_dict()}")
    else:
        logger.info("[FIREBASE_TEST_APP] Dokument bot_config/last_fetch_state NIE ISTNIEJE.")
    logger.info("[FIREBASE_TEST_APP] Test odczytu z Firestore ZAKOŃCZONY.")

except Exception as e:
    logger.error(f"[FIREBASE_TEST_APP] WYSTĄPIŁ BŁĄD podczas inicjalizacji Firebase lub testu Firestore: {e}", exc_info=True)

@flask_app.route('/')
def health_check():
    logger.info(f"[FIREBASE_TEST_APP] Żądanie na / o {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    return "Firebase Test App is running!", 200

if __name__ == '__main__':
    # Ten blok nie jest używany przez Gunicorna w App Engine
    logger.info("[FIREBASE_TEST_APP] Uruchamianie serwera Flask lokalnie...")
    flask_app.run(host='0.0.0.0', port=8081)
else:
    logger.info(f"[FIREBASE_TEST_APP] Moduł zaimportowany przez Gunicorna. Czas: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")