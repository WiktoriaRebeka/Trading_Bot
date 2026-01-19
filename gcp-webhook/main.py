# Lokalizacja: /gcp-webhook/main.py

import os
import logging
import hmac
import hashlib
import requests
import json
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
    logger.info("--- START PRZETWARZANIA (RAW MODE) ---")

    if request.method != 'POST':
        return ('Method Not Allowed', 405)

    try:
        # 1. Pobieramy surowe bajty (omijamy get_json)
        raw_bytes = request.get_data()
        if not raw_bytes:
            logger.error("BŁĄD: Body jest całkowicie puste.")
            return ("Empty body", 400)

        # 2. Czyścimy bajty (usuwamy znaki NULL \x00 i białe znaki)
        clean_bytes = raw_bytes.replace(b'\x00', b'').strip()
        
        # 3. Dekodujemy na tekst
        try:
            raw_text = clean_bytes.decode("utf-8")
        except UnicodeDecodeError:
            logger.error(f"BŁĄD DEKODOWANIA. Raw: {clean_bytes[:50]}")
            return ("Invalid encoding", 400)

        logger.info(f"ODEBRANO RAW TEXT: {raw_text}")

        # 4. Parsujemy JSON ręcznie
        try:
            alert_data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            logger.error(f"BŁĄD PARSOWANIA JSON: {e}. Text: {raw_text[:100]}")
            return ("Invalid JSON format", 400)

        # 5. Weryfikacja tokena (Bezpieczeństwo!)
        received_token = alert_data.pop('secret_token', None)
        if not received_token or not hmac.compare_digest(str(received_token), WEBHOOK_SECRET):
            logger.error(f"BŁĄD AUTORYZACJI. Token: {received_token}")
            return ("Unauthorized", 403)

        # 6. Walidacja pól (id_symbol, id_direction)
        required = ["id_symbol", "id_direction", "entry", "sl", "tp"]
        if not all(k in alert_data for k in required):
            logger.error(f"BRAK PÓL. Mam: {list(alert_data.keys())}")
            return ("Missing fields", 400)

        logger.info(f"✅ SYGNAŁ ZWERYFIKOWANY: {alert_data['id_symbol']}")

        # 7. Zapis do Firestore
        client = get_db_client()
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP
        doc_ref = client.collection('alerts').document()
        doc_ref.set(alert_data)

        # 8. Przekazanie do bota
        try:
            resp = requests.post(BOT_SERVICE_URL, json=alert_data, timeout=5)
            logger.info(f"Bot Service Response: {resp.status_code}")
        except Exception as e:
            logger.error(f"Nie udało się powiadomić bota: {e}")

        return ("OK", 201)

    except Exception as e:
        logger.error(f"KRYTYCZNY BŁĄD: {e}", exc_info=True)
        return ("Internal Error", 500)