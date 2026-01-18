# Lokalizacja: /gcp-webhook/main.py

import os
import logging
import hmac
import hashlib
import requests  # DODANO: do przesyłania sygnału dalej
from google.cloud import firestore

logging.basicConfig(level=logging.INFO, format='%(asctime)s - WEBHOOK - %(levelname)s - %(message)s')

PROJECT_ID = os.environ.get('GCP_PROJECT', 'trading-bot-463318')
DATABASE_NAME = "trading-bot-data"
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET_TOKEN')

# URL Twojego bota (Cloud Run) - sprawdź czy ten adres się zgadza!
BOT_SERVICE_URL = "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts"

db = None

def get_db_client():
    global db
    if db is None:
        try:
            db = firestore.Client(project=PROJECT_ID, database=DATABASE_NAME)
            return db
        except Exception as e:
            logging.critical(f"Błąd Firestore: {e}", exc_info=True)
            raise
    return db

def firestore_webhook_receiver(request):
    if request.method != 'POST':
        return ('Dozwolone są tylko żądania POST', 405)

    if not WEBHOOK_SECRET:
        return ("Błąd konfiguracji serwera", 500)

    try:
        # force=True jest kluczowe, bo Sierra nie wysyła nagłówka application/json
        alert_data = request.get_json(silent=True, force=True)
        
        if not alert_data or not isinstance(alert_data, dict):
            return ("Nieprawidłowe żądanie: Oczekiwano obiektu JSON", 400)

        received_token = alert_data.pop('secret_token', None)
        if not received_token or not hmac.compare_digest(str(received_token), WEBHOOK_SECRET):
            logging.error(f"Błąd autoryzacji. Token: '{received_token}'")
            return ("Błąd autoryzacji", 403)
        
    except Exception as e:
        logging.error(f"Błąd parsowania: {e}")
        return ("Nieprawidłowe żądanie", 400)

    try:
        # ZMIENIONO: Klucze muszą pasować do tego, co wysyła C++
        required_keys = ["id_symbol", "id_direction", "entry", "sl", "tp"]
        if not all(key in alert_data for key in required_keys):
            logging.error(f"Brak kluczy. Otrzymano: {list(alert_data.keys())}")
            return (f"Brakujące klucze. Wymagane: {required_keys}", 400)

        # 1. Zapis do Firestore (Logowanie zdarzenia)
        client = get_db_client()
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP
        doc_ref = client.collection('alerts').document()
        doc_ref.set(alert_data)
        logging.info(f"Alert zapisany w Firestore: {doc_ref.id}")

        # 2. NATYCHMIASTOWY PUSH DO BOTA (To uruchamia trading)
        try:
            # Przekazujemy cały payload do bota na Cloud Run
            response = requests.post(BOT_SERVICE_URL, json=alert_data, timeout=5)
            if response.status_code == 200:
                logging.info("Sygnał pomyślnie przekazany do trading-bot-service.")
            else:
                logging.error(f"Bot zwrócił błąd {response.status_code}: {response.text}")
        except Exception as e:
            logging.error(f"Nie udało się połączyć z botem: {e}")

        return ("Alert przetworzony", 201)

    except Exception as e:
        logging.error(f"Błąd krytyczny: {e}", exc_info=True)
        return ("Wewnętrzny błąd serwera", 500)