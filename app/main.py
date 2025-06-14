# TRADING_BOT/app/main.py
from flask import Flask, jsonify, request
import logging
import sys
import os
from datetime import datetime 

# --- Importy modułów aplikacji ---
from .firebase_client import initialize_firebase, get_db
from . import constants
from . import state_manager
from . import fetch_from_firestore
from . import positions_logger 
from . import bot_logic

# --- Konfiguracja Logowania ---
# ZMIANA: Poziom DEBUG, aby widzieć wszystkie szczegółowe logi z innych modułów
logging.basicConfig(
    stream=sys.stdout, 
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("app.main") 

# --- Inicjalizacja Aplikacji i Firebase ---
logger.info(f"--- ŁADOWANIE app/main.py (Wersja: {os.getenv('GAE_VERSION', 'Lokalna')}) ---")

firebase_initialized = False
try:
    logger.info("Wywołuję initialize_firebase() z firebase_client...")
    if initialize_firebase(): 
        firebase_initialized = True
        logger.info("initialize_firebase() zakończone sukcesem.")
        test_db = get_db()
        if test_db:
            logger.info("get_db() pomyślnie zwróciło klienta Firestore.")
        else:
            logger.error("KRYTYCZNY BŁĄD: initialize_firebase() zwróciło True, ale get_db() zwróciło None!")
            firebase_initialized = False 
    else:
        logger.error("KRYTYCZNY BŁĄD: initialize_firebase() zwróciło False.")
except Exception as e_init:
    logger.error(f"KRYTYCZNY BŁĄD: Wyjątek podczas inicjalizacji w app/main.py: {e_init}", exc_info=True)
    firebase_initialized = False

# Tworzymy instancję Flask
app = Flask(__name__)
logger.info("Instancja Flask 'app' utworzona.")


# --- Endpointy HTTP ---

@app.route('/')
def health_check():
    logger.debug("Odebrano żądanie na / (health_check)")
    status_message = "Firebase OK" if firebase_initialized else "Firebase FAILED"
    gae_version = os.getenv('GAE_VERSION', 'N/A')
    return f"Trading Bot App (App Engine - Wersja: {gae_version}) is running! {status_message}. UTC: {datetime.utcnow().isoformat()}", 200

@app.route('/_ah/warmup')
def warmup():
    logger.info("Obsługa żądania /_ah/warmup")
    if not firebase_initialized:
        logger.warning("Warmup: Firebase nie było zainicjowane, próba ponownej inicjalizacji...")
        initialize_firebase() 
    
    try:
        if get_db(): 
            get_db().collection(constants.BOT_CONFIG_COLLECTION).limit(1).get()
            logger.info("Warmup: Pomyślnie wykonano testowe zapytanie do Firestore.")
    except Exception as e_warmup:
        logger.error(f"Warmup: Błąd podczas testowego zapytania do Firestore: {e_warmup}", exc_info=True)
    return '', 200


@app.route('/run-bot-cycle', methods=['GET', 'POST'])
def run_bot_cycle_endpoint():
    source_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    logger.info(f"--- ROZPOCZĘCIE CYKLU BOTA (Żądanie od: {source_ip}) ---")

    if not firebase_initialized:
        logger.error("Nie można uruchomić cyklu bota: Firebase nie jest zainicjowane poprawnie.")
        return jsonify({"status": "error", "message": "Firestore not initialized properly"}), 500

    try:
        # ===============================================================
        # === POPRAWIONA LOGIKA POBIERANIA I AKTUALIZACJI TIMESTAMP ===
        # ===============================================================
        
        # 1. Odczytaj ostatni timestamp jako obiekt datetime
        current_last_ts_dt = fetch_from_firestore.load_last_processed_timestamp()
        
        # 2. Pobierz nowe alerty i najnowszy timestamp (również jako datetime)
        newly_fetched_alerts, new_max_ts_dt = fetch_from_firestore.fetch_new_alerts_since(current_last_ts_dt)
        
        alerts_processed_count = 0
        if newly_fetched_alerts:
            alerts_processed_count = len(newly_fetched_alerts)
            logger.info(f"Przetwarzanie {alerts_processed_count} nowych alertów...")
            for alert_data in newly_fetched_alerts:
                state_manager.process_alert(alert_data) 
            
            # 3. Zapisz nowy timestamp, jeśli jest nowszy od starego.
            # Porównanie obiektów datetime jest bezpieczne i niezawodne.
            if new_max_ts_dt > current_last_ts_dt:
                fetch_from_firestore.save_last_processed_timestamp(new_max_ts_dt)

        # ===============================================================
        # === GŁÓWNA LOGIKA BOTA (BEZ ZMIAN) ===
        # ===============================================================
        
        active_alert_symbols = state_manager.get_all_alert_symbols()
        active_position_symbols = state_manager.get_all_position_symbols() 
        all_active_symbols = set(active_alert_symbols) | set(active_position_symbols)
        
        if not all_active_symbols:
            logger.info("Brak aktywnych symboli. Zakończenie cyklu.")
            return jsonify({"status": "success", "message": "No active symbols to process."}), 200
            
        logger.info(f"Aktywne symbole do przetworzenia: {list(all_active_symbols)}")

        all_current_prices = {}
        if all_active_symbols:
            all_current_prices = bot_logic.get_all_prices_for_category()
            if not all_current_prices:
                logger.warning("Nie udało się pobrać aktualnych cen. Logika bota może nie działać poprawnie.")
        
        logger.info("--- Faza 1: Sprawdzanie nowych setupów ---")
        for symbol in all_active_symbols:
            bot_logic.check_for_new_setups(symbol) 
        
        logger.info("--- Faza 2: Monitorowanie istniejących pozycji ---")
        bot_logic.monitor_positions(all_current_prices) 
        
        # Opcjonalne podsumowanie stanu (tylko lokalnie)
        if os.getenv('GAE_ENV') != 'standard':
            state_manager.print_state_summary() 

        logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---")
        return jsonify({
            "status": "success", 
            "message": "Bot cycle completed.",
            "alerts_processed": alerts_processed_count,
            "active_symbols_processed": list(all_active_symbols)
        }), 200

    except Exception as e:
        logger.error(f"Krytyczny błąd podczas wykonywania cyklu bota: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Critical error during bot cycle: {e}"}), 500


if __name__ == '__main__':
    logger.info("Uruchamianie serwera Flask lokalnie (dla testów)...")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)
else:
    logger.info(f"Moduł {__name__} zaimportowany, prawdopodobnie przez Gunicorn.")

logger.info(f"--- ZAKOŃCZONO ŁADOWANIE app/main.py (Wersja: {os.getenv('GAE_VERSION', 'Lokalna')}) ---")