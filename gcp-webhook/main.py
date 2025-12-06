# Lokalizacja: /gcp-webhook/main.py

import os
import logging
import hmac
import hashlib
from google.cloud import firestore

logging.basicConfig(level=logging.INFO, format='%(asctime)s - WEBHOOK - %(levelname)s - %(message)s')

PROJECT_ID = os.environ.get('GCP_PROJECT')
DATABASE_NAME = "trading-bot-data"
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET_TOKEN')
db = None

def get_db_client():
    """Inicjalizuje i buforuje klienta Firestore."""
    global db
    if db is None:
        try:
            db = firestore.Client(project=PROJECT_ID, database=DATABASE_NAME)
            db.collection('_test_connection_').limit(1).get()
            logging.info("Klient Firestore pomyślnie zainicjalizowany.")
        except Exception as e:
            logging.critical(f"Krytyczny błąd inicjalizacji klienta Firestore: {e}", exc_info=True)
            db = None
            raise
    return db

def firestore_webhook_receiver(request):
    """Główna funkcja (entry point) dla Google Cloud Function z autoryzacją."""
    if request.method != 'POST':
        return ('Dozwolone są tylko żądania POST', 405)

    # --- KROK 1: WERYFIKACJA SEKRETU (NAJWAŻNIEJSZY ELEMENT BEZPIECZEŃSTWA) ---
    if not WEBHOOK_SECRET:
        logging.critical("KRYTYCZNY BŁĄD BEZPIECZEŃSTWA: Sekret WEBHOOK_SECRET_TOKEN nie jest skonfigurowany!")
        return ("Błąd konfiguracji serwera", 500)

    try:
        alert_data = request.get_json(silent=True)
        if not alert_data or not isinstance(alert_data, dict):
            logging.warning("Otrzymano puste lub nieprawidłowe żądanie JSON.")
            return ("Nieprawidłowe żądanie: Oczekiwano obiektu JSON", 400)

        # Wyciągamy token z wiadomości. Jeśli go nie ma, autoryzacja zawiedzie.
        received_token = alert_data.pop('secret_token', None)
        
        # Używamy hmac.compare_digest dla bezpiecznego porównania
        if not received_token or not hmac.compare_digest(str(received_token), WEBHOOK_SECRET):
            logging.error(f"NIEUDANA AUTORYZACJA WEBHOOKA. Otrzymany token: '{received_token}'")
            return ("Błąd autoryzacji", 403)
        
        logging.info("Autoryzacja webhooka pomyślna.")

    except Exception as e:
        logging.error(f"Błąd podczas parsowania JSON lub autoryzacji: {e}", exc_info=True)
        return ("Nieprawidłowe żądanie", 400)
    
    # --- KROK 2: PRZETWARZANIE ALERTU PO AUTORYZACJI ---
    try:
        client = get_db_client()
    except Exception:
        return ("Błąd serwera: Klient Firestore niedostępny", 500)

    try:
        # Lista wymaganych pól biznesowych (bez tokena)
        required_keys = ["symbol", "directionCode", "entry", "sl", "tp"]
        if not all(key in alert_data for key in required_keys):
            logging.error(f"Brak wymaganych kluczy w alercie po autoryzacji: {alert_data}")
            return (f"Brakujące klucze w alercie. Wymagane: {required_keys}", 400)

        logging.info(f"Odebrano poprawny alert dla symbolu: {alert_data.get('symbol')}")
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP
        
        doc_ref = client.collection('alerts').document()
        doc_ref.set(alert_data)
        
        logging.info(f"Pomyślnie zapisano alert. ID dokumentu: {doc_ref.id}")
        return ("Alert zapisany pomyślnie", 201)
    except Exception as e:
        logging.error(f"Błąd przetwarzania alertu po autoryzacji: {e}", exc_info=True)
        return ("Wewnętrzny błąd serwera", 500)