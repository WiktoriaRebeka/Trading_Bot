# W pliku: /main.py
# ZASTĄP CAŁĄ ZAWARTOŚĆ PLIKU

import logging
import sys

# Krok 1: Załaduj konfigurację PRZED WSZYSTKIM INNYM.
# To zapewni, że inne moduły (jak constants) będą miały dostęp do zmiennych środowiskowych.
from config_loader import load_config
load_config()

# Krok 2: Importuj resztę modułów
from flask import Flask, jsonify
from datetime import datetime, timezone
from pydantic import ValidationError
from firebase_client import initialize_firebase, get_db
import constants
import fetch_from_firestore
import bot_logic
from models import AlertData, SetupData

# Konfiguracja logowania
logging.basicConfig(
    stream=sys.stdout, level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Inicjalizacja aplikacji
firebase_initialized = initialize_firebase()
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Trading Bot App is running.", 200

@app.route('/run-bot-cycle', methods=['GET', 'POST'])
def run_bot_cycle_endpoint():
    logger.info("--- ROZPOCZĘCIE CYKLU BOTA ---")
    if not firebase_initialized:
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500
    try:
        # ... reszta logiki pozostaje bez zmian ...
        current_last_ts_dt = fetch_from_firestore.load_last_processed_timestamp()
        newly_fetched_alerts, new_max_ts_dt = fetch_from_firestore.fetch_new_alerts_since(current_last_ts_dt)
        if newly_fetched_alerts:
            db = get_db()
            for alert_dict in newly_fetched_alerts:
                try:
                    alert_data = AlertData.parse_obj(alert_dict)
                    if alert_data.direction_code == 1: alert_data.direction = "LONG"
                    elif alert_data.direction_code == -1: alert_data.direction = "SHORT"
                    else: continue
                    new_setup = SetupData(alert_data=alert_data, updated_at=datetime.now(timezone.utc))
                    doc_ref = db.collection(constants.SETUP_COLLECTION).document(alert_data.symbol)
                    doc_ref.set(new_setup.dict(by_alias=True))
                    logger.info(f"[{alert_data.symbol}] Zarejestrowano/zaktualizowano aktywny setup.")
                except ValidationError as e:
                    logger.error(f"Błąd walidacji alertu. ID: {alert_dict.get('id')}. Błędy: {e}")
            if new_max_ts_dt > current_last_ts_dt:
                fetch_from_firestore.save_last_processed_timestamp(new_max_ts_dt)
        bot_logic.run_trading_logic()
        logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---")
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500