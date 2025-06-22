# TRADING_BOT/main.py 

from flask import Flask, jsonify, request
import logging
import sys
import os
from datetime import datetime 

# --- Importy modułów aplikacji ---
from firebase_client import initialize_firebase, get_db
import constants
import state_manager
import fetch_from_firestore
import bot_logic
# --- Konfiguracja Logowania ---
logging.basicConfig(
    stream=sys.stdout, 
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("app.main") 

# --- Inicjalizacja Aplikacji i Firebase ---
firebase_initialized = False
try:
    if initialize_firebase(): 
        firebase_initialized = True
        logger.info("Inicjalizacja Firebase zakończona sukcesem.")
    else:
        logger.error("KRYTYCZNY BŁĄD: initialize_firebase() zwróciło False.")
except Exception as e_init:
    logger.error(f"KRYTYCZNY BŁĄD: Wyjątek podczas inicjalizacji Firebase: {e_init}", exc_info=True)

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
    logger.info("--- ROZPOCZĘCIE CYKLU BOTA (Strategia OB Only v2) ---")

    if not firebase_initialized:
        logger.error("Błąd krytyczny: Firebase nie jest zainicjowane.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        # 1. Pobierz i przetwórz nowe alerty Order Block
        current_last_ts_dt = fetch_from_firestore.load_last_processed_timestamp()
        newly_fetched_alerts, new_max_ts_dt = fetch_from_firestore.fetch_new_alerts_since(current_last_ts_dt)
        
        if newly_fetched_alerts:
            for alert_data in newly_fetched_alerts:
                if alert_data.get('type') == 'OrderBlock':
                    direction_code = alert_data.get('directionCode')
                    if direction_code == 1:
                        alert_data['direction'] = "LONG"
                    elif direction_code == -1:
                        alert_data['direction'] = "SHORT"
                    else:
                        logger.warning(f"Otrzymano alert z nieprawidłowym directionCode: {direction_code}")
                        continue
                    
                    state_manager.set_active_setup(alert_data['symbol'], alert_data)
            
            if new_max_ts_dt > current_last_ts_dt:
                fetch_from_firestore.save_last_processed_timestamp(new_max_ts_dt)

        # 2. Pobierz ceny dla wszystkich AKTYWNYCH symboli
        active_symbols = state_manager.get_all_active_symbols()
        if not active_symbols:
            logger.info("Brak aktywnych symboli do monitorowania.")
            return jsonify({"status": "success", "message": "No active symbols"}), 200
        
        all_current_prices = bot_logic.get_all_prices_for_category()
        if not all_current_prices:
            logger.warning("Nie udało się pobrać cen rynkowych. Pomijam cykl logiki.")
            return jsonify({"status": "warning", "message": "Failed to fetch prices"}), 200
        
        # 3. Uruchom główną pętlę logiki
        bot_logic.run_trading_logic(all_current_prices)

        logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---")
        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"Krytyczny błąd w cyklu bota: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
   

@app.route('/test-firebase-connection')
def test_firebase_connection():
    """
    Ten endpoint służy wyłącznie do diagnozowania problemu z inicjalizacją Firebase.
    """
    # Celowo nie używamy globalnej flagi, tylko za każdym razem próbujemy od nowa.
    logger.info("--- ROZPOCZĘCIE TESTU POŁĄCZENIA /test-firebase-connection ---")
    try:
        # Używamy dokładnie tej samej funkcji inicjalizującej, co bot
        from .firebase_client import initialize_firebase
        is_success = initialize_firebase()

        if is_success:
            logger.info("!!! TESTOWY ENDPOINT: Inicjalizacja Firebase ZAKOŃCZONA SUKCESEM !!!")
            return "SUCCESS: Firebase connection was established.", 200
        else:
            # To się stanie, jeśli initialize_firebase() zwróci False, ale bez wyjątku
            logger.error("!!! TESTOWY ENDPOINT: initialize_firebase() zwróciło False !!!")
            return "FAILURE: initialize_firebase() returned False.", 500

    except Exception as e:
        # To jest najważniejsza część - złapie i zaloguje DOKŁADNY błąd
        logger.critical(f"!!! TESTOWY ENDPOINT: KRYTYCZNY WYJĄTEK PODCZAS INICJALIZACJI: {e}", exc_info=True)
        return f"CRITICAL EXCEPTION: {e}", 500


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)