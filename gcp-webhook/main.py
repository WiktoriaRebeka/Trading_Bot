# Lokalizacja: /gcp-webhook/main.py

import os
import logging
import hmac
import hashlib
import requests
from google.cloud import firestore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get('GCP_PROJECT', 'trading-bot-463318')
DATABASE_NAME = "trading-bot-data"
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET_TOKEN')
# URL Twojego bota
BOT_SERVICE_URL = "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts"

db = None

def get_db_client():
    global db
    if db is None:
        db = firestore.Client(project=PROJECT_ID, database=DATABASE_NAME)
    return db

def firestore_webhook_receiver(request):
    if request.method != 'POST':
        return ('Method Not Allowed', 405)

    # force=True jest KRYTYCZNE dla Sierra Chart
    alert_data = request.get_json(silent=True, force=True)
    
    if not alert_data:
        raw_body = request.get_data(as_text=True)
        logger.error(f"BŁĄD: Brak JSONA. Surowe dane: {raw_body[:100]}")
        return ("No JSON", 400)

    # Autoryzacja
    received_token = alert_data.pop('secret_token', None)
    if not received_token or not hmac.compare_digest(str(received_token), WEBHOOK_SECRET):
        logger.error(f"Błąd tokena! Otrzymano: {received_token}")
        return ("Unauthorized", 403)

    try:
        symbol = alert_data.get('id_symbol')
        if not symbol:
            return ("Missing id_symbol", 400)

        logger.info(f"✅ ODEBRANO SYGNAŁ: {symbol}")

        # 1. Zapis do Firestore
        client = get_db_client()
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP
        client.collection('alerts').document().set(alert_data)

        # 2. PUSH do bota (trading-bot-service)
        try:
            requests.post(BOT_SERVICE_URL, json=alert_data, timeout=5)
            logger.info("Sygnał przekazany do bota.")
        except Exception as e:
            logger.error(f"Bot nie odpowiedział: {e}")

        return ("OK", 201)
    except Exception as e:
        logger.error(f"Błąd: {e}")
        return ("Error", 500)