# Lokalizacja: /gcp-webhook/main.py

import os
import logging
import hmac
import hashlib
import requests
from google.cloud import firestore

logging.basicConfig(level=logging.INFO, format='%(asctime)s - WEBHOOK - %(levelname)s - %(message)s')

PROJECT_ID = os.environ.get('GCP_PROJECT', 'trading-bot-463318')
DATABASE_NAME = "trading-bot-data"
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET_TOKEN')

# URL Twojego bota (Cloud Run)
BOT_SERVICE_URL = "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts"

db = None

def get_db_client():
    global db
    if db is None:
        db = firestore.Client(project=PROJECT_ID, database=DATABASE_NAME)
    return db

def firestore_webhook_receiver(request):
    if request.method != 'POST':
        return ('Dozwolone są tylko żądania POST', 405)

    if not WEBHOOK_SECRET:
        return ("Błąd konfiguracji serwera", 500)

    # Pobieramy dane (force=True zignoruje brak nagłówka JSON z Sierry)
    alert_data = request.get_json(silent=True, force=True)
    
    if not alert_data:
        # Logujemy co przyszło, żeby wiedzieć co to za "puste" żądania
        raw_data = request.get_data(as_text=True)
        logging.warning(f"Otrzymano nieprawidłowy format lub puste żądanie. Raw: {raw_data[:100]}")
        return ("Oczekiwano JSON", 400)

    # Weryfikacja tokena
    received_token = alert_data.pop('secret_token', None)
    if not received_token or not hmac.compare_digest(str(received_token), WEBHOOK_SECRET):
        logging.error(f"Błąd autoryzacji. Token: '{received_token}'")
        return ("Błąd autoryzacji", 403)

    try:
        # MAPOWANIE PÓL: Musi pasować do Twojego C++ (id_symbol, id_direction)
        required_keys = ["id_symbol", "id_direction", "entry", "sl", "tp"]
        if not all(key in alert_data for key in required_keys):
            logging.error(f"Brak kluczy. Otrzymano: {list(alert_data.keys())}")
            return (f"Brakujące klucze: {required_keys}", 400)

        symbol = alert_data['id_symbol']
        logging.info(f"✅ Odebrano poprawny alert dla: {symbol}")

        # 1. Zapis do Firestore
        client = get_db_client()
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP
        doc_ref = client.collection('alerts').document()
        doc_ref.set(alert_data)

        # 2. PUSH do bota (trading-bot-service)
        try:
            # Przekazujemy dane dalej do bota
            resp = requests.post(BOT_SERVICE_URL, json=alert_data, timeout=5)
            logging.info(f"Bot Response: {resp.status_code}")
        except Exception as e:
            logging.error(f"Nie udało się powiadomić bota: {e}")

        return ("Alert zapisany i przekazany", 201)

    except Exception as e:
        logging.error(f"Błąd: {e}", exc_info=True)
        return ("Błąd serwera", 500)