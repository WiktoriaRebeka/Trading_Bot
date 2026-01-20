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
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET_TOKEN', '').strip()
BOT_SERVICE_URL = "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts"

db = None

def get_db_client():
    global db
    if db is None:
        db = firestore.Client(project=PROJECT_ID, database=DATABASE_NAME)
    return db

def firestore_webhook_receiver(request):
    logger.info("--- START PRZETWARZANIA (RAW MODE v2.2) ---")

    if request.method != 'POST':
        return ('Method Not Allowed', 405)

    try:
        # 1. Pobieramy surowe bajty i czyścimy ze śmieci binarnych C++
        raw_bytes = request.get_data()
        if not raw_bytes:
            logger.error("BŁĄD: Body jest całkowicie puste.")
            return ("Empty body", 400)

        clean_bytes = raw_bytes.replace(b'\x00', b'').strip()
        
        # 2. Dekodujemy i parsujemy JSON
        try:
            raw_text = clean_bytes.decode("utf-8")
            alert_data = json.loads(raw_text)
        except Exception as e:
            logger.error(f"BŁĄD PARSOWANIA: {e}")
            return ("Invalid JSON", 400)

        # 3. Weryfikacja tokena (Bezpieczeństwo)
        received_token = alert_data.pop('secret_token', None)
        if not received_token or not hmac.compare_digest(str(received_token), WEBHOOK_SECRET):
            logger.error(f"BŁĄD AUTORYZACJI. Token: {received_token}")
            return ("Unauthorized", 403)

        # 4. Walidacja pól i Price Sanity Check (Bramkarz)
        symbol = alert_data.get('id_symbol', '')
        entry_price = float(alert_data.get('entry', 0))

        if "BTC" in symbol and entry_price < 10000:
            logger.error(f"ODRZUCONO: Nierealna cena BTC: {entry_price}")
            return ("Invalid Price", 422)
        
        if "ETH" in symbol and entry_price < 500:
            logger.error(f"ODRZUCONO: Nierealna cena ETH: {entry_price}")
            return ("Invalid Price", 422)

        if not symbol or entry_price <= 0:
            logger.error(f"BŁĄD: Brak symbolu lub ceny <= 0")
            return ("Missing data", 400)

        logger.info(f"✅ SYGNAŁ ZWERYFIKOWANY: {symbol} @ {entry_price}")

        # 5. PRZEKAZANIE DO BOTA (Zanim dodamy Sentinel)
        try:
            resp = requests.post(BOT_SERVICE_URL, json=alert_data, timeout=5)
            logger.info(f"Bot Service Response: {resp.status_code}")
        except Exception as e:
            logger.error(f"Nie udało się powiadomić bota: {e}")

        # 6. ZAPIS DO FIRESTORE (Na samym końcu)
        try:
            client = get_db_client()
            alert_data['received_at'] = firestore.SERVER_TIMESTAMP
            client.collection('alerts').document().set(alert_data)
            logger.info(f"Zapisano w Firestore.")
        except Exception as e:
            logger.error(f"Błąd zapisu Firestore: {e}")

        return ("OK", 201)

    except Exception as e:
        logger.error(f"KRYTYCZNY BŁĄD: {e}", exc_info=True)
        return ("Internal Error", 500)