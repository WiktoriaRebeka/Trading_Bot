# TRADING_BOT/app/main.py

from flask import Flask, jsonify
import logging
import sys

# --- NOWE PODEJŚCIE DO IMPORTÓW I INICJALIZACJI ---
from .firebase_client import initialize_firebase, get_db
# Importujemy moduły, które będą używane w endpointach
from . import fetch_from_firestore
from . import state_manager
from . import bot_logic
from . import positions_logger # positions_logger sam pobierze db z firebase_client

# Konfiguracja logowania
logging.basicConfig(
    stream=sys.stdout, 
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- INICJALIZACJA APLIKACJI I FIREBASE ---
logger.info("--- Ładowanie skryptu app/main.py ---")

# Wywołujemy naszą scentralizowaną funkcję inicjalizującą
firebase_initialized = initialize_firebase()

# Tworzymy instancję Flask
app = Flask(__name__)
logger.info("Instancja Flask 'app' utworzona.")

# --- ENDPOINTY FLASK ---

@app.route('/')
def health_check():
    """Podstawowy health check, który mówi, czy bot działa i czy połączył się z Firebase."""
    logger.info("Odebrano żądanie na / (health_check)")
    if firebase_initialized:
        return "Trading Bot App is running! Firebase init OK.", 200
    else:
        return "Trading Bot App is running, but Firebase initialization FAILED.", 500

@app.route('/_ah/warmup')
def warmup():
    """Endpoint dla Google App Engine, aby 'rozgrzać' instancję."""
    logger.info("Obsługa żądania /_ah/warmup")
    # Możemy tu umieścić lekkie operacje, np. ponowne upewnienie się, że DB jest dostępne
    if firebase_initialized:
        try:
            get_db().collection('bot_config').limit(1).get()
            logger.info("Warmup: Pomyślnie wykonano testowe zapytanie do Firestore.")
        except Exception as e:
            logger.error(f"Warmup: Błąd podczas testowego zapytania do Firestore: {e}", exc_info=True)
    return '', 200

@app.route('/run-bot-cycle', methods=['GET', 'POST'])
def run_bot_cycle_endpoint():
    """Główny endpoint wywoływany przez Cloud Scheduler do uruchomienia cyklu bota."""
    logger.info("--- ROZPOCZĘCIE CYKLU BOTA ---")

    if not firebase_initialized:
        logger.error("Nie można uruchomić cyklu bota: Firebase nie jest zainicjowane.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        # 1. Pobierz i przetwórz nowe alerty z Firestore
        current_last_ts = fetch_from_firestore.load_last_processed_timestamp()
        newly_fetched_alerts, new_max_ts = fetch_from_firestore.fetch_new_alerts_since(current_last_ts)
        
        alerts_processed_count = 0
        if newly_fetched_alerts:
            logger.info(f"Pobrano {len(newly_fetched_alerts)} nowych alertów. Przetwarzanie...")
            for alert_data in newly_fetched_alerts:
                state_manager.process_alert(alert_data)
            alerts_processed_count = len(newly_fetched_alerts)
            
            if new_max_ts > current_last_ts:
                fetch_from_firestore.save_last_processed_timestamp(new_max_ts)
                logger.info(f"Zaktualizowano ostatni przetworzony timestamp na: {new_max_ts}")
        else:
            logger.info("Brak nowych alertów do przetworzenia.")

        # 2. Zbierz listę wszystkich aktywnych symboli (z alertów i pozycji)
        # To zapobiega sytuacji, w której bot "zapomina" o pozycji, jeśli nie ma dla niej nowych alertów
        active_alert_symbols = state_manager.get_all_alert_symbols()
        active_position_symbols = state_manager.get_all_position_symbols()
        all_active_symbols = set(active_alert_symbols) | set(active_position_symbols)
        
        if not all_active_symbols:
            logger.info("Brak aktywnych symboli. Zakończenie cyklu.")
            return jsonify({"status": "success", "message": "No active symbols to process."}), 200
            
        logger.info(f"Aktywne symbole do przetworzenia: {list(all_active_symbols)}")

        # 3. Pobierz ceny RAZ dla wszystkich symboli
        all_current_prices = bot_logic.get_all_prices_for_category()

        # 4. Sprawdź nowe setupy dla każdego aktywnego symbolu
        logger.info("--- Faza 1: Sprawdzanie nowych setupów ---")
        for symbol in all_active_symbols:
            bot_logic.check_for_new_setups(symbol)

        # 5. Monitoruj WSZYSTKIE pozycje (planowane i otwarte) naraz
        logger.info("--- Faza 2: Monitorowanie istniejących pozycji ---")
        bot_logic.monitor_positions(all_current_prices)
        
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

logger.info("--- Zakończono ładowanie skryptu app/main.py ---")