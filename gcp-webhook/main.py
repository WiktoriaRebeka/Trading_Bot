# Lokalizacja: /gcp-webhook/main.py

import os
import logging
import hmac
import hashlib
import requests
from google.cloud import firestore

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get('GCP_PROJECT', 'trading-bot-463318')
DATABASE_NAME = "trading-bot-data"
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET_TOKEN')
BOT_SERVICE_URL = "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts"

db = None

def get_db_client():
    global db
    if db is None:
        db = firestore.Client(project=PROJECT_ID, database=DATABASE_NAME)
    return db

def firestore_webhook_receiver(request):
    # --- TO JEST TEST PRAWDY ---
    logger.info("!!! TEST WERSJI 2.0 - ODEBRANO SYGNAŁ Z SIERRY !!!")

    if request.method != 'POST':
        return ('Method Not Allowed', 405)

    # force=True to jedyny sposób na dane z Sierra Chart!
    alert_data = request.get_json(silent=True, force=True)
    
    if not alert_data:
        raw_body = request.get_data(as_text=True)
        logger.error(f"BŁĄD: Flask nie widzi JSONa. Surowe dane: {raw_body[:100]}")
        return ("Brak danych JSON", 400)

    # Autoryzacja
    received_token = alert_data.pop('secret_token', None)
    if not received_token or not hmac.compare_digest(str(received_token), WEBHOOK_SECRET):
        logger.error(f"Błąd tokena! Otrzymano: {received_token}")
        return ("Unauthorized", 403)

    try:
        # Mapowanie pól z Twojego C++
        symbol = alert_data.get('id_symbol')
        direction = alert_data.get('id_direction')
        
        if not symbol or not direction:
            logger.error(f"Brak kluczowych pól! Mam tylko: {list(alert_data.keys())}")
            return ("Missing fields", 400)

        logger.info(f"✅ SYGNAŁ POPRAWNY: {symbol} {direction}")

        # 1. Zapis do Firestore
        client = get_db_client()
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP
        client.collection('alerts').document().set(alert_data)

        # 2. PUSH do bota
        try:
            resp = requests.post(BOT_SERVICE_URL, json=alert_data, timeout=5)
            logger.info(f"Bot odpowiedział statusem: {resp.status_code}")
        except Exception as e:
            logger.error(f"Bot nieosiągalny: {e}")

        return ("OK", 201)

    except Exception as e:
        logger.error(f"KRYTYCZNY BŁĄD: {e}", exc_info=True)
        return ("Internal Error", 500)