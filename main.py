# /trading_bot/main.py (WERSJA FINALNA v5.2)

from flask import Flask, jsonify
import logging
import sys
import os
from datetime import datetime, timezone

# --- Importy modułów aplikacji ---
from firebase_client import initialize_firebase, get_db
import constants
import fetch_from_firestore
import bot_logic

# --- Konfiguracja Logowania ---
logging.basicConfig(
    stream=sys.stdout, 
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("app.main") 

# --- Inicjalizacja Aplikacji i Firebase ---
firebase_initialized = initialize_firebase()

app = Flask(__name__)

@app.route('/')
def health_check():
    status_message = "Firebase OK" if firebase_initialized else "Firebase FAILED"
    gae_version = os.getenv('GAE_VERSION', 'N/A')
    return f"Trading Bot App (Wersja: {gae_version}) is running! {status_message}.", 200

@app.route('/_ah/warmup')
def warmup():
    logger.info("Obsługa żądania /_ah/warmup")
    return '', 200

@app.route('/run-bot-cycle', methods=['GET', 'POST'])
def run_bot_cycle_endpoint():
    logger.info("--- ROZPOCZĘCIE CYKLU BOTA (Architektura v5.2) ---")

    if not firebase_initialized:
        logger.error("Błąd krytyczny: Firebase nie jest zainicjowane.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        # === ETAP 1: ZARZĄDZANIE NOWYMI SETUPAMI ===
        current_last_ts_dt = fetch_from_firestore.load_last_processed_timestamp()
        newly_fetched_alerts, new_max_ts_dt = fetch_from_firestore.fetch_new_alerts_since(current_last_ts_dt)
        
        if newly_fetched_alerts:
            db = get_db()
            for alert_data in newly_fetched_alerts:
                if alert_data.get('type') == 'OrderBlock':
                    direction_code = alert_data.get('directionCode')
                    if direction_code == 1: alert_data['direction'] = "LONG"
                    elif direction_code == -1: alert_data['direction'] = "SHORT"
                    else: continue
                    
                    symbol = alert_data.get('symbol')
                    if not symbol: continue

                    doc_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)
                    
                    # Nowy alert unieważnia stary setup i resetuje jego stan.
                    new_setup_state = {
                        "alert_data": alert_data,
                        "entry_attempts": 0,
                        "is_position_open_on_this_setup": False,
                        "is_reset_needed_after_loss": False,
                        "updated_at": datetime.now(timezone.utc)
                    }
                    doc_ref.set(new_setup_state)
                    logger.info(f"[{symbol}] Zarejestrowano/zaktualizowano aktywny setup w '{constants.SETUP_COLLECTION}'.")
            
            if new_max_ts_dt > current_last_ts_dt:
                fetch_from_firestore.save_last_processed_timestamp(new_max_ts_dt)

        # === ETAP 2: EGZEKUCJA GŁÓWNEJ LOGIKI TRADINGOWEJ ===
        bot_logic.run_trading_logic()

        logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---")
        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"Krytyczny błąd w głównym cyklu bota: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)