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
# from . import bot_logic # Zostaw na razie zakomentowane
from . import constants

logging.basicConfig(stream=sys.stdout, 
                    level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - [TRADING_BOT_V1FIX] - %(message)s') # Nowy prefix
logger = logging.getLogger(__name__)

logger.info(f"--- SCRIPT app/main.py (V1FIX) LOADED ---")

firebase_initialized_successfully = False
db_client = None # To będzie nasz główny klient Firestore
try:
    if not firebase_admin._apps:
        logger.info("[INIT_V1FIX] Próba inicjalizacji Firebase Admin SDK...")
        firebase_admin.initialize_app()
        logger.info("[INIT_V1FIX] Inicjalizacja Firebase Admin SDK ZAKOŃCZONA SUKCESEM.")
    else:
        logger.info("[INIT_V1FIX] Firebase Admin SDK już zainicjowane.")
    
    db_client = firestore.client() # Inicjujemy klienta tutaj
    logger.info("[INIT_V1FIX] Połączenie z Firestore ZAKOŃCZONE SUKCESEM.")
    
    # Ustawiamy klienta Firestore w module fetch_from_firestore
    fetch_from_firestore.set_firestore_client(db_client) # <--- WAŻNE
    # Możesz zrobić podobnie dla positions_logger, jeśli on też potrzebuje db_client
    # import .positions_logger
    # positions_logger.set_firestore_client(db_client)

    firebase_initialized_successfully = True
except Exception as e:
    logger.error(f"[INIT_V1FIX_ERROR] BŁĄD Firebase/Firestore: {e}", exc_info=True)

app = Flask(__name__)
logger.info("Instancja Flask 'app' (V1FIX) utworzona.")

@app.route('/')
def health_check_v1fix():
    logger.info(f"Żądanie na / (health_check_v1fix)")
    if firebase_initialized_successfully:
        return "Trading Bot App (V1FIX) is running! Firebase init OK.", 200
    else:
        return "Trading Bot App (V1FIX) is running! Firebase init FAILED.", 500

@app.route('/_ah/warmup')
def warmup_v1fix():
    logger.info("Obsługa żądania /_ah/warmup (V1FIX)")
    # Można tu dodać np. pierwsze wywołanie load_last_processed_timestamp, aby "rozgrzać"
    if firebase_initialized_successfully:
        try:
            logger.info(f"Warmup: Próba odczytu timestampa: {fetch_from_firestore.load_last_processed_timestamp()}")
        except Exception as e:
            logger.error(f"Warmup: Błąd przy load_last_processed_timestamp: {e}")
    return '', 200

@app.route('/run-bot-cycle', methods=['GET', 'POST'])
def run_bot_cycle_endpoint_v1fix():
    logger.info("Odebrano żądanie na /run-bot-cycle (V1FIX - logika bota częściowo aktywna)")

    if not firebase_initialized_successfully or not db_client: # db_client jest teraz lokalny dla main.py
        logger.error("Nie można uruchomić cyklu bota: Firebase/Firestore nie jest zainicjowane.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        logger.info("--- ROZPOCZĘCIE CYKLU BOTA (V1FIX) ---")
        
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
        
        # Na razie nie uruchamiamy bot_logic
        logger.info("Logika bot_logic jest na razie pomijana w tym teście.")
        
        state_manager.print_state_summary()

        logger.info("--- ZAKOŃCZENIE CYKLU BOTA (V1FIX) ---")
        return jsonify({
            "status": "success", 
            "message": "Bot cycle (V1FIX - fetcher and state_manager only) completed.",
            "alerts_processed": alerts_processed_count
        }), 200

    except Exception as e:
        logger.error(f"Błąd podczas wykonywania cyklu bota (V1FIX): {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Error during bot cycle (V1FIX): {e}"}), 500
    
logger.info("--- END OF SCRIPT app/main.py (V1FIX) DEFINITIONS ---")