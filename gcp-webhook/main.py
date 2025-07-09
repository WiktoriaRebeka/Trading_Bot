# Lokalizacja: gcp-webhook/main.py

import os
import logging
from google.cloud import firestore
from flask import jsonify


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

PROJECT_ID = os.environ.get('GCP_PROJECT', 'trading-bot-463318')
DATABASE_NAME = "trading-bot-data"
db = None

def get_db_client():
    """
    Inicjalizuje i buforuje klienta Firestore.
    To jest zalecany wzorzec dla Cloud Functions.
    """
    global db
    if db is None:
        try:
            logging.info(f"Inicjalizacja klienta Firestore dla projektu '{PROJECT_ID}' i bazy '{DATABASE_NAME}'.")
            db = firestore.Client(project=PROJECT_ID, database=DATABASE_NAME)
            db.collection('_test_connection_').limit(1).get()
            logging.info("Klient Firestore pomyślnie zainicjalizowany.")
        except Exception as e:
            logging.critical(f"Krytyczny błąd inicjalizacji klienta Firestore: {e}", exc_info=True)
            db = None
            raise  
    return db

def firestore_webhook_receiver(request):
    """Główna funkcja (entry point) dla Google Cloud Function."""
    if request.method != 'POST':
        return ('Dozwolone są tylko żądania POST', 405)

    try:
        client = get_db_client()
    except Exception:
        return ("Błąd serwera: Klient Firestore niedostępny", 500)

    try:
        alert_data = request.get_json(silent=True)
        if not alert_data or not isinstance(alert_data, dict):
            logging.warning("Otrzymano puste lub nieprawidłowe żądanie JSON.")
            return ("Nieprawidłowe żądanie: Oczekiwano obiektu JSON", 400)
        required_keys = ["symbol", "directionCode", "entry", "sl", "tp"]
        if not all(key in alert_data for key in required_keys):
            logging.error(f"Brak wymaganych kluczy w alercie: {alert_data}")
            return (f"Brakujące klucze w alercie. Wymagane: {required_keys}", 400)

        logging.info(f"Odebrano alert: {alert_data}")
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP
        
        doc_ref = client.collection('alerts').document()
        doc_ref.set(alert_data)
        
        logging.info(f"Pomyślnie zapisano alert. ID: {doc_ref.id}")
        return ("Alert zapisany pomyślnie", 201)
    except Exception as e:
        logging.error(f"Błąd przetwarzania alertu: {e}", exc_info=True)
        return ("Wewnętrzny błąd serwera", 500)