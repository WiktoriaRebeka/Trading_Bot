# TRADING_BOT/app/main.py
from flask import Flask, jsonify # Dodajemy jsonify
import firebase_admin
from firebase_admin import firestore # Usunąłem 'credentials', bo na GAE nie są potrzebne
import logging
import os
import sys # Dodajemy sys dla konfiguracji loggingu
import time
import threading # Dodajemy threading, jeśli zdecydujesz się na pętlę w tle (opcja)

# Importy modułów Twojego bota
from . import fetch_from_firestore  # Używamy importu relatywnego
from . import state_manager
from . import bot_logic
from . import constants # Jeśli app/constants.py zawiera zmienne odczytywane przez os.getenv

# --- Konfiguracja Logowania ---
# To jest ważne, aby logi były widoczne w App Engine
logging.basicConfig(stream=sys.stdout, 
                    level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - [TRADING_BOT_APP] - %(message)s')
logger = logging.getLogger(__name__)

logger.info(f"--- SCRIPT app/main.py LOADED ---")
logger.info(f"Python version: {sys.version}")
logger.info(f"GAE_ENV: {os.getenv('GAE_ENV')}")
logger.info(f"CWD: {os.getcwd()}")

# --- Inicjalizacja Firebase Admin SDK ---
# Na App Engine Standard, użyje domyślnych poświadczeń środowiska.
firebase_initialized_successfully = False
db_client = None # Zmieniamy nazwę z 'db' na 'db_client' dla jasności
try:
    if not firebase_admin._apps: # Sprawdź, czy aplikacja nie została już zainicjowana
        logger.info("[INIT] Próba inicjalizacji Firebase Admin SDK (domyślne poświadczenia)...")
        firebase_admin.initialize_app()
        logger.info("[INIT] Inicjalizacja Firebase Admin SDK ZAKOŃCZONA SUKCESEM.")
    else:
        logger.info("[INIT] Firebase Admin SDK już zainicjowane.")
    
    # Ustawienie klienta Firestore dla tego modułu, jeśli fetch_from_firestore go nie ustawił globalnie
    # lub jeśli chcemy mieć pewność, że jest dostępny tutaj.
    # Lepszym podejściem jest jednak, aby fetch_from_firestore.py inicjował i udostępniał 'db'.
    # Na razie zakładamy, że 'fetch_from_firestore.db' jest dostępne po imporcie.
    if fetch_from_firestore.db:
        db_client = fetch_from_firestore.db
        logger.info("[INIT] Używam klienta Firestore zainicjowanego przez fetch_from_firestore.py.")
        firebase_initialized_successfully = True
    else:
        # Fallback, jeśli z jakiegoś powodu fetch_from_firestore.db nie jest dostępne
        logger.warning("[INIT_WARN] Klient Firestore z fetch_from_firestore.py nie jest dostępny. Próba inicjalizacji lokalnej.")
        db_client = firestore.client()
        logger.info("[INIT] Połączenie z Firestore (lokalny fallback) ZAKOŃCZONE SUKCESEM.")
        firebase_initialized_successfully = True

except Exception as e:
    logger.error(f"[INIT_ERROR] WYSTĄPIŁ BŁĄD podczas inicjalizacji Firebase lub Firestore: {e}", exc_info=True)
    firebase_initialized_successfully = False

# --- Instancja Aplikacji Flask ---
# Nazwa 'app' jest tym, czego szuka Gunicorn w 'app.main:app'
app = Flask(__name__)
logger.info("Instancja Flask 'app' utworzona.")

# --- Endpointy HTTP ---

@app.route('/')
def health_check():
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    logger.info(f"Żądanie na / (health_check) o {timestamp}")
    if firebase_initialized_successfully:
        return f"Trading Bot App (App Engine) is running! Firebase init OK. UTC: {timestamp}", 200
    else:
        return f"Trading Bot App (App Engine) is running! Firebase init FAILED. UTC: {timestamp}", 500

@app.route('/_ah/warmup')
def warmup():
    logger.info("Obsługa żądania /_ah/warmup")
    # Tutaj można umieścić logikę inicjalizacyjną, która powinna się wykonać raz
    # np. upewnienie się, że Firebase jest zainicjowane (już to robimy wyżej globalnie)
    # lub załadowanie jakichś początkowych danych.
    # Na razie, jeśli Firebase jest OK, zwracamy sukces.
    if firebase_initialized_successfully:
        logger.info("Warmup: Firebase OK.")
        return '', 200
    else:
        logger.error("Warmup: Firebase initialization FAILED.")
        # Można zwrócić błąd, aby App Engine wiedział, że instancja nie jest gotowa,
        # ale to może prowadzić do cyklu restartów, jeśli błąd jest trwały.
        # Na razie zwracamy 200, ale logujemy błąd.
        return 'Warmup: Firebase init FAILED.', 200 # lub 500, jeśli chcemy zasygnalizować błąd

# --- Endpoint do uruchamiania cyklu bota (dla Cloud Scheduler) ---
@app.route('/run-bot-cycle', methods=['GET', 'POST']) # POST jest bezpieczniejszy dla triggerów
def run_bot_cycle_endpoint():
    logger.info("Odebrano żądanie na /run-bot-cycle")

    if not firebase_initialized_successfully or not db_client:
        logger.error("Nie można uruchomić cyklu bota: Firebase/Firestore nie jest zainicjowane.")
        return jsonify({"status": "error", "message": "Firestore not initialized"}), 500

    try:
        logger.info("--- ROZPOCZĘCIE CYKLU BOTA ---")
        
        # 1. Pobierz nowe alerty
        # Używamy funkcji z fetch_from_firestore.py
        current_last_ts = fetch_from_firestore.load_last_processed_timestamp()
        logger.info(f"Aktualny ostatni przetworzony timestamp: {current_last_ts}")
        
        newly_fetched_alerts, new_max_ts_from_batch_str = fetch_from_firestore.fetch_new_alerts_since(current_last_ts)
        
        alerts_processed_count = 0
        if newly_fetched_alerts:
            logger.info(f"Pobrano {len(newly_fetched_alerts)} nowych alertów.")
            for alert_data in newly_fetched_alerts:
                state_manager.process_alert(alert_data) # Przetwarzanie alertu i aktualizacja stanu w pamięci
                alerts_processed_count += 1
            
            if new_max_ts_from_batch_str > current_last_ts:
                fetch_from_firestore.save_last_processed_timestamp(new_max_ts_from_batch_str)
                logger.info(f"Zaktualizowano ostatni przetworzony timestamp na: {new_max_ts_from_batch_str}")
        else:
            logger.info("Brak nowych alertów do przetworzenia.")

        # 2. Uruchom logikę bota dla każdego relevantnego symbolu
        # Symbole mogą pochodzić z przetworzonych alertów lub z listy aktywnych/monitorowanych pozycji
        # Dla uproszczenia, weźmy symbole z alertów i z aktualnie zarządzanych pozycji
        active_symbols = set(state_manager.get_all_alert_symbols())
        active_symbols.update(state_manager.get_all_position_symbols())
        
        logger.info(f"Aktywne symbole do przetworzenia przez logikę bota: {list(active_symbols)}")

        for symbol in active_symbols:
            logger.info(f"Przetwarzanie logiki dla symbolu: {symbol}")
            bot_logic.check_new_long_entries(symbol)
            bot_logic.check_new_short_entries(symbol)
            bot_logic.monitor_planned_positions(symbol)
            bot_logic.monitor_opened_positions(symbol)
        
        state_manager.print_state_summary() # Opcjonalne, do debugowania stanu w pamięci

        logger.info("--- ZAKOŃCZENIE CYKLU BOTA ---")
        return jsonify({
            "status": "success", 
            "message": "Bot cycle completed.",
            "alerts_processed": alerts_processed_count,
            "active_symbols_processed": list(active_symbols)
        }), 200

    except Exception as e:
        logger.error(f"Błąd podczas wykonywania cyklu bota: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Error during bot cycle: {e}"}), 500

# --- Opcjonalnie: Uruchomienie pętli fetchera w tle (mniej zalecane dla App Engine Standard) ---
# Jeśli chcesz, aby fetcher_loop działał ciągle w tle, a nie tylko na żądanie Cloud Scheduler.
# UWAGA: To może być problematyczne w App Engine Standard.
# fetcher_thread = None
# def start_fetcher_thread():
#    global fetcher_thread
#    if firebase_initialized_successfully and (fetcher_thread is None or not fetcher_thread.is_alive()):
#        logger.info("Uruchamianie wątku fetcher_loop...")
#        fetcher_thread = threading.Thread(target=fetch_from_firestore.fetcher_loop, daemon=True)
#        fetcher_thread.start()
#    elif not firebase_initialized_successfully:
#        logger.error("Nie można uruchomić wątku fetcher_loop: Firebase nie zainicjowane.")
#    elif fetcher_thread and fetcher_thread.is_alive():
#        logger.info("Wątek fetcher_loop już działa.")

# Wywołanie startu wątku - tylko jeśli nie debugujesz lokalnie z auto-reloaderem Flaska
# if os.getenv('GAE_ENV', '').startswith('standard'): # Tylko na App Engine
#    logger.info("Środowisko App Engine - próba uruchomienia wątku fetchera przy starcie (jeśli skonfigurowano).")
#    # start_fetcher_thread() # Odkomentuj, jeśli chcesz użyć tej metody

# --- Uruchomienie lokalne (nie dla Gunicorna) ---
if __name__ == '__main__':
    logger.info("Uruchamianie serwera Flask lokalnie (dla testów)...")
    # start_fetcher_thread() # Opcjonalne uruchomienie wątku lokalnie dla testów
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), debug=True)
else:
    # Ten blok jest wykonywany, gdy Gunicorn importuje 'app'
    logger.info(f"Moduł {__name__} zaimportowany, prawdopodobnie przez Gunicorn.")
    # if os.getenv('GAE_ENV', '').startswith('standard'): # Tylko na App Engine
    #    logger.info("Środowisko App Engine - próba uruchomienia wątku fetchera po imporcie przez Gunicorn (jeśli skonfigurowano).")
        # start_fetcher_thread() # Odkomentuj, jeśli chcesz użyć tej metody

logger.info("--- END OF SCRIPT app/main.py DEFINITIONS ---")