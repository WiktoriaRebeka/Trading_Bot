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
from . import bot_logic

# --- Konfiguracja Logowania ---
logging.basicConfig(
    stream=sys.stdout, 
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s'
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
logger.info("Instancja Flask 'app' utworzona.")


@app.route('/')
def health_check():
    """Podstawowy endpoint sprawdzający, czy aplikacja działa."""
    status_message = "Firebase OK" if firebase_initialized else "Firebase FAILED"
    gae_version = os.getenv('GAE_VERSION', 'Lokalna')
    return f"Trading Bot App (Wersja: {gae_version}) is running! {status_message}. UTC: {datetime.utcnow().isoformat()}", 200

@app.route('/_ah/warmup')
def warmup():
    """Standardowy endpoint GAE do rozgrzewania instancji."""
    logger.info("Obsługa żądania /_ah/warmup")
    return '', 200, {'Content-Type': 'text/plain'}

@app.route('/run-bot-cycle', methods=['GET', 'POST'])
def run_bot_cycle_endpoint():
    """Główny endpoint wywoływany przez Cloud Scheduler, koordynujący cykl bota."""
    source_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    logger.info(f"--- ROZPOCZĘCIE CYKLU BOTA (Żądanie od: {source_ip}) ---")

    if not firebase_initialized:
        logger.error("Nie można uruchomić cyklu bota: Firebase nie jest zainicjowane poprawnie.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        # === ETAP 1: POBIERANIE NOWYCH DANYCH ===
        last_ts = fetch_from_firestore.load_last_processed_timestamp()
        new_alerts, new_max_ts = fetch_from_firestore.fetch_new_alerts_since(last_ts)
        
        # === ETAP 2: PRZETWARZANIE NOWYCH ALERTÓW I AKTUALIZACJA STANU ===
        if new_alerts:
            bot_logic.process_new_alerts(new_alerts)
            if new_max_ts > last_ts:
                fetch_from_firestore.save_last_processed_timestamp(new_max_ts)
        else:
            logger.info("Brak nowych alertów do przetworzenia.")

        # === ETAP 3: GŁÓWNA LOGIKA STRATEGII ===
        # Zbierz wszystkie symbole, którymi musimy się zająć
        symbols_from_alerts = state_manager.get_all_alert_symbols()
        symbols_from_positions = state_manager.get_all_position_symbols()
        all_active_symbols = sorted(list(set(symbols_from_alerts) | set(symbols_from_positions)))
        
        if not all_active_symbols:
            logger.info("Brak aktywnych symboli. Zakończenie cyklu.")
            return jsonify({"status": "success", "message": "No active symbols to process."}), 200
            
        logger.info(f"Aktywne symbole do przetworzenia: {all_active_symbols}")

        # Pobierz ceny dla wszystkich symboli za jednym razem
        all_current_prices = bot_logic.get_all_prices_for_category()
        if not all_current_prices:
            logger.warning("Nie udało się pobrać aktualnych cen z Bybit. Niektóre funkcje mogą nie działać poprawnie.")
        
        # --- Faza 3a: Cykl Strategii (sprawdzanie mitigacji i planowanie) ---
        bot_logic.run_strategy_cycle(all_active_symbols, all_current_prices) 
        
        # --- Faza 3b: Monitorowanie istniejących pozycji (otwieranie, zamykanie) ---
        bot_logic.monitor_positions(all_current_prices) 
        
        # Opcjonalne podsumowanie stanu w logach (przydatne do debugowania)
        if os.getenv('GAE_ENV') != 'standard': # Nie drukuj na produkcji, chyba że potrzebujesz
             state_manager.print_state_summary()

        logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---")
        return jsonify({
            "status": "success", 
            "message": "Bot cycle completed.",
            "alerts_processed": len(new_alerts),
            "active_symbols_processed": all_active_symbols
        }), 200

    except Exception as e:
        logger.error(f"Krytyczny błąd podczas wykonywania cyklu bota: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Critical error during bot cycle: {e}"}), 500

if __name__ == '__main__':
    # Uruchomienie lokalne do testów
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)