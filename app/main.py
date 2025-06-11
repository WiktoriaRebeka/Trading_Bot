# TRADING_BOT/app/main.py
from flask import Flask, jsonify, request
import logging
import sys
import os
from datetime import datetime # Potrzebne dla isinstance(new_max_ts, datetime)

# --- Importy modułów aplikacji ---
from .firebase_client import initialize_firebase, get_db
from . import constants
from . import state_manager
from . import fetch_from_firestore
from . import positions_logger # Zakładamy, że używa get_db() lub ma własny mechanizm
from . import bot_logic

# --- Konfiguracja Logowania ---
# Użyjemy bardziej ogólnego formatu, aby nazwa loggera była widoczna
logging.basicConfig(
    stream=sys.stdout, 
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Tworzymy głównego loggera dla tego modułu
logger = logging.getLogger("app.main") # Nadajemy mu konkretną nazwę

# --- Inicjalizacja Aplikacji i Firebase ---
logger.info("--- ŁADOWANIE app/main.py ---")
logger.info(f"GAE_ENV: {os.getenv('GAE_ENV')}")

# Scentralizowana inicjalizacja Firebase
firebase_initialized = False
try:
    logger.info("Wywołuję initialize_firebase() z firebase_client...")
    if initialize_firebase(): # Wywołuje funkcję z firebase_client.py
        firebase_initialized = True
        logger.info("initialize_firebase() zakończone sukcesem (zwróciło True).")
        # Testowe pobranie klienta DB, aby upewnić się, że get_db() działa
        test_db = get_db()
        if test_db:
            logger.info("get_db() pomyślnie zwróciło klienta Firestore.")
        else:
            # To nie powinno się zdarzyć, jeśli initialize_firebase() zwróciło True i ustawiło db_client
            logger.error("KRYTYCZNY BŁĄD: initialize_firebase() zwróciło True, ale get_db() zwróciło None!")
            firebase_initialized = False # Na wszelki wypadek
    else:
        logger.error("KRYTYCZNY BŁĄD: initialize_firebase() zwróciło False. Sprawdź logi z firebase_client.py.")
except Exception as e_init:
    logger.error(f"KRYTYCZNY BŁĄD: Wyjątek podczas wywoływania initialize_firebase() lub get_db() w app/main.py: {e_init}", exc_info=True)
    firebase_initialized = False

# Tworzymy instancję Flask
app = Flask(__name__) # Nazwa 'app' jest ważna dla Gunicorna
logger.info("Instancja Flask 'app' utworzona.")


# --- Endpointy HTTP ---

@app.route('/')
def health_check():
    """Podstawowy health check."""
    logger.info("Odebrano żądanie na / (health_check)")
    status_message = "Firebase OK" if firebase_initialized else "Firebase FAILED"
    return f"Trading Bot App (App Engine - Full) is running! {status_message}. UTC: {datetime.utcnow().isoformat()}", 200

@app.route('/_ah/warmup')
def warmup():
    """Endpoint dla Google App Engine do 'rozgrzewania' instancji."""
    logger.info("Obsługa żądania /_ah/warmup")
    if not firebase_initialized:
        # Spróbuj ponownie zainicjować, jeśli warmup jest wywoływany przed pełnym startem
        logger.warning("Warmup: Firebase nie było zainicjowane, próba ponownej inicjalizacji...")
        initialize_firebase() # Nie przypisujemy tu wyniku, bo główna inicjalizacja już była
    
    # Można dodać lekkie operacje inicjalizacyjne lub testowe zapytanie do DB
    try:
        if get_db(): # Sprawdź, czy klient jest dostępny
            get_db().collection(constants.BOT_CONFIG_COLLECTION).limit(1).get()
            logger.info("Warmup: Pomyślnie wykonano testowe zapytanie do Firestore.")
    except Exception as e_warmup:
        logger.error(f"Warmup: Błąd podczas testowego zapytania do Firestore: {e_warmup}", exc_info=True)
        # Nie zwracaj błędu 5xx, aby uniknąć problemów z deploymentem, jeśli to tylko warmup
    return '', 200

@app.route('/run-bot-cycle', methods=['GET', 'POST'])
def run_bot_cycle_endpoint():
    """Główny endpoint wywoływany przez Cloud Scheduler do uruchomienia cyklu bota."""
    # Sprawdzenie, czy to żądanie pochodzi z Cloud Scheduler (zalecane dla bezpieczeństwa)
    # Można to zrobić sprawdzając specjalny nagłówek, np. X-CloudScheduler
    is_scheduler_request = request.headers.get('X-CloudScheduler', type=bool)
    if os.getenv('GAE_ENV') == 'standard' and not is_scheduler_request and request.remote_addr != "0.1.0.1": # 0.1.0.1 to adres App Engine dla zadań cron
        # W środowisku produkcyjnym można dodać bardziej rygorystyczne sprawdzanie
        # logger.warning(f"Żądanie na /run-bot-cycle od nieautoryzowanego źródła: {request.remote_addr}")
        # return jsonify({"status": "error", "message": "Unauthorized"}), 403
        pass # Na razie pozwalamy na testy z przeglądarki

    logger.info(f"--- ROZPOCZĘCIE CYKLU BOTA (Żądanie od: {request.remote_addr}) ---")

    if not firebase_initialized:
        logger.error("Nie można uruchomić cyklu bota: Firebase nie jest zainicjowane poprawnie.")
        return jsonify({"status": "error", "message": "Firestore not initialized properly"}), 500

    try:
        # 1. Pobierz i przetwórz nowe alerty z Firestore
        current_last_ts = fetch_from_firestore.load_last_processed_timestamp()
        logger.info(f"Aktualny ostatni przetworzony timestamp: {current_last_ts}")
        
        newly_fetched_alerts, new_max_ts_iso = fetch_from_firestore.fetch_new_alerts_since(current_last_ts)
        # Zakładamy, że fetch_new_alerts_since zawsze zwraca new_max_ts_iso jako string ISO
        
        alerts_processed_count = 0
        if newly_fetched_alerts:
            logger.info(f"Pobrano {len(newly_fetched_alerts)} nowych alertów. Przetwarzanie...")
            for alert_data in newly_fetched_alerts:
                state_manager.process_alert(alert_data) 
            alerts_processed_count = len(newly_fetched_alerts)
            
            if new_max_ts_iso > current_last_ts:
                fetch_from_firestore.save_last_processed_timestamp(new_max_ts_iso)
                logger.info(f"Zaktualizowano ostatni przetworzony timestamp na: {new_max_ts_iso}")
        else:
            logger.info("Brak nowych alertów do przetworzenia.")

        # 2. Zbierz listę wszystkich aktywnych symboli
        active_alert_symbols = state_manager.get_all_alert_symbols()
        active_position_symbols = state_manager.get_all_position_symbols() # Upewnij się, że ta funkcja pobiera z Firestore
        all_active_symbols = set(active_alert_symbols) | set(active_position_symbols)
        
        if not all_active_symbols:
            logger.info("Brak aktywnych symboli. Zakończenie cyklu.")
            return jsonify({"status": "success", "message": "No active symbols to process."}), 200
            
        logger.info(f"Aktywne symbole do przetworzenia: {list(all_active_symbols)}")

        # 3. Pobierz ceny RAZ dla wszystkich symboli (jeśli są aktywne symbole)
        all_current_prices = {}
        if all_active_symbols:
            all_current_prices = bot_logic.get_all_prices_for_category()
            if not all_current_prices:
                logger.warning("Nie udało się pobrać aktualnych cen. Logika bota może nie działać poprawnie.")
        
        # 4. Sprawdź nowe setupy i monitoruj pozycje
        logger.info("--- Sprawdzanie nowych setupów i monitorowanie pozycji ---")
        for symbol in all_active_symbols:
            logger.info(f"Przetwarzanie logiki dla symbolu: {symbol}")
            bot_logic.check_for_new_setups(symbol) 
        
        # Wywołaj monitorowanie pozycji po sprawdzeniu wszystkich setupów
        # Zakładając, że monitor_positions wie, które symbole monitorować (np. z get_all_position_symbols)
        bot_logic.monitor_positions(all_current_prices) 
        
        state_manager.print_state_summary() # Loguje stan alertów w pamięci (opcjonalne dla debugowania)

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

# Ten blok jest głównie dla testów lokalnych, Gunicorn go nie używa.
if __name__ == '__main__':
    logger.info("Uruchamianie serwera Flask lokalnie (dla testów)...")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)
else:
    logger.info(f"Moduł {__name__} zaimportowany, prawdopodobnie przez Gunicorn.")

logger.info("--- ZAKOŃCZONO ŁADOWANIE app/main.py ---")