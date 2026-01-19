# Lokalizacja: /gcp-webhook/main.py

import os
import logging
import hmac
import hashlib
import requests
from google.cloud import firestore

# Inicjalizacja logowania dla Cloud Run/Functions
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get('GCP_PROJECT', 'trading-bot-463318')
DATABASE_NAME = "trading-bot-data"
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET_TOKEN')

# URL serwisu wykonawczego (Bot Service)
BOT_SERVICE_URL = "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts"

db = None

def get_db_client():
    global db
    if db is None:
        # Inicjalizacja klienta Firestore z jawnym wskazaniem bazy danych
        db = firestore.Client(project=PROJECT_ID, database=DATABASE_NAME)
    return db

def firestore_webhook_receiver(request):
    """
    Entry point dla sygnałów PUSH z Sierra Chart.
    """
    if request.method != 'POST':
        return ('Method Not Allowed', 405)

    # Pobranie danych z wymuszeniem parsowania JSON (Sierra nie wysyła nagłówka Content-Type)
    alert_data = request.get_json(silent=True, force=True)
    
    if not alert_data:
        raw_body = request.get_data(as_text=True)
        logger.error(f"BŁĄD: Nie wykryto JSON. Surowy body: {raw_body[:200]}")
        return ("Oczekiwano formatu JSON", 400)

    # Weryfikacja tokena bezpieczeństwa
    received_token = alert_data.pop('secret_token', None)
    if not received_token or not hmac.compare_digest(str(received_token), WEBHOOK_SECRET):
        logger.error(f"Błąd autoryzacji. Otrzymano token: {received_token}")
        return ("Unauthorized", 403)

    try:
        # Walidacja pól wysyłanych przez kod C++ (id_symbol, id_direction)
        symbol = alert_data.get('id_symbol')
        if not symbol:
            logger.error(f"Brak pola id_symbol w danych: {alert_data}")
            return ("Missing id_symbol", 400)

        logger.info(f"✅ ODEBRANO SYGNAŁ PUSH DLA: {symbol}")

        # 1. Archiwizacja sygnału w Firestore
        client = get_db_client()
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP
        client.collection('alerts').document().set(alert_data)

        # 2. Przekazanie sygnału do Trading Bot Service (Cloud Run)
        try:
            # Przesyłamy dane dalej do bota, który wykona logikę DRY RUN / LIVE
            resp = requests.post(BOT_SERVICE_URL, json=alert_data, timeout=5)
            logger.info(f"Bot Service odpowiedział statusem: {resp.status_code}")
        except Exception as e:
            logger.error(f"Nie udało się połączyć z Bot Service: {e}")

        return ("Alert processed", 201)

    except Exception as e:
        logger.error(f"Błąd krytyczny webhooka: {e}", exc_info=True)
        return ("Internal Server Error", 500)