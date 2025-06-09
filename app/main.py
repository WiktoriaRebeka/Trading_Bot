# TRADING_BOT/app/main.py
from flask import Flask, jsonify
import firebase_admin
from firebase_admin import firestore
import logging
import os
import sys
import time
import threading 

# Importy modułów Twojego bota
from . import fetch_from_firestore
from . import state_manager
from . import bot_logic # <--- ODKOMENTOWANE (tak jak w mojej poprzedniej odpowiedzi)
from . import constants

logging.basicConfig(stream=sys.stdout, 
                    level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - [TRADING_BOT_FULL_LOGIC] - %(message)s') # Zmieniony prefix dla tego testu
logger = logging.getLogger(__name__) # Poprawione na __name__

logger.info(f"--- SCRIPT app/main.py (FULL_LOGIC_TEST) LOADED ---")

firebase_initialized_successfully = False
db_client = None
try:
    if not firebase_admin._apps:
        logger.info("[INIT_FULL_LOGIC] Próba inicjalizacji Firebase Admin SDK...")
        firebase_admin.initialize_app()
        logger.info("[INIT_FULL_LOGIC] Inicjalizacja Firebase Admin SDK ZAKOŃCZONA SUKCESEM.")
    else:
        logger.info("[INIT_FULL_LOGIC] Firebase Admin SDK już zainicjowane.")
    
    db_client = firestore.client()
    logger.info("[INIT_FULL_LOGIC] Połączenie z Firestore ZAKOŃCZONE SUKCESEM.")
    
    fetch_from_firestore.set_firestore_client(db_client)
    # Również dla positions_logger, jeśli go używasz i potrzebuje db_client
    # from . import positions_logger # Upewnij się, że jest importowany, jeśli set_firestore_client ma być na nim wywołane
    # positions_logger.set_firestore_client(db_client) # Odkomentuj, jeśli positions_logger tego wymaga

    firebase_initialized_successfully = True
except Exception as e:
    logger.error(f"[INIT_FULL_LOGIC_ERROR] BŁĄD Firebase/Firestore: {e}", exc_info=True)
    firebase_initialized_successfully = False # Poprawione

app = Flask(__name__) # Poprawione na __name__
logger.info("Instancja Flask 'app' (FULL_LOGIC_TEST) utworzona.")

@app.route('/')
def health_check_full_logic():
    logger.info(f"Żądanie na / (health_check_full_logic)")
    if firebase_initialized_successfully:
        return "Trading Bot App (FULL_LOGIC_TEST) is running! Firebase init OK.", 200
    else:
        return "Trading Bot App (FULL_LOGIC_TEST) is running! Firebase init FAILED.", 500

@app.route('/_ah/warmup')
def warmup_full_logic():
    logger.info("Obsługa żądania /_ah/warmup (FULL_LOGIC_TEST)")
    if firebase_initialized_successfully:
        try:
            logger.info(f"Warmup: Próba odczytu timestampa: {fetch_from_firestore.load_last_processed_timestamp()}")
        except Exception as e:
            logger.error(f"Warmup: Błąd przy load_last_processed_timestamp: {e}")
    return '', 200

@app.route('/run-bot-cycle', methods=['GET', 'POST'])
def run_bot_cycle_endpoint_full_logic():
    logger.info("Odebrano żądanie na /run-bot-cycle (FULL_LOGIC_TEST - PEŁNA LOGIKA AKTYWNA)")

    if not firebase_initialized_successfully or not db_client:
        logger.error("Nie można uruchomić cyklu bota: Firebase/Firestore nie jest zainicjowane.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        logger.info("--- ROZPOCZĘCIE CYKLU BOTA (FULL_LOGIC_TEST) ---")
        
        current_last_ts = fetch_from_firestore.load_last_processed_timestamp()
        logger.info(f"Aktualny ostatni przetworzony timestamp: {current_last_ts}")
        
        newly_fetched_alerts, new_max_ts_from_batch_str = fetch_from_firestore.fetch_new_alerts_since(current_last_ts)
        
        alerts_processed_count = 0
        if newly_fetched_alerts:
            logger.info(f"Pobrano {len(newly_fetched_alerts)} nowych alertów.")
            for alert_data in newly_fetched_alerts:
                state_manager.process_alert(alert_data)
                alerts_processed_count += 1
            
            if new_max_ts_from_batch_str > current_last_ts:
                fetch_from_firestore.save_last_processed_timestamp(new_max_ts_from_batch_str)
                logger.info(f"Zaktualizowano ostatni przetworzony timestamp na: {new_max_ts_from_batch_str}")
        else:
            logger.info("Brak nowych alertów do przetworzenia.")
        
        active_symbols = set(state_manager.get_all_alert_symbols())
        active_symbols.update(state_manager.get_all_position_symbols())
        
        logger.info(f"Aktywne symbole do przetworzenia przez logikę bota: {list(active_symbols)}")

        # === LOGIKA BOTA JEST TERAZ AKTYWNA ===
        for symbol in active_symbols:
            logger.info(f"Przetwarzanie logiki dla symbolu: {symbol}")
            bot_logic.check_new_long_entries(symbol)    # ODKOMENTOWANE
            bot_logic.check_new_short_entries(symbol)   # ODKOMENTOWANE
            bot_logic.monitor_planned_positions(symbol) # ODKOMENTOWANE
            bot_logic.monitor_opened_positions(symbol)  # ODKOMENTOWANE
        # =====================================
        
        state_manager.print_state_summary()

        logger.info("--- ZAKOŃCZENIE CYKLU BOTA (FULL_LOGIC_TEST) ---")
        return jsonify({
            "status": "success", 
            "message": "Bot cycle (FULL LOGIC ACTIVE) completed.",
            "alerts_processed": alerts_processed_count,
            "active_symbols_processed": list(active_symbols)
        }), 200

    except Exception as e:
        logger.error(f"Błąd podczas wykonywania cyklu bota (FULL_LOGIC_TEST): {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Error during bot cycle (FULL LOGIC ACTIVE): {e}"}), 500
    
logger.info("--- END OF SCRIPT app/main.py (FULL_LOGIC_TEST) DEFINITIONS ---")