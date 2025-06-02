# TRADING_BOT/app/main.py
import threading
import time
import sys
import os
from flask import Flask
import requests  # Dodany import dla testowego zapytania
import traceback # Dodany import dla tracebacku

# Ustawienie ścieżek
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Importy modułów aplikacji
from app import bot_logic, state_manager
from app.fetch_from_firestore import fetcher_loop, db as firestore_db_client
from app.constants import BOT_LOOP_INTERVAL_SECONDS, BYBIT_API_URL_V5_TICKERS # Dodano BYBIT_API_URL_V5_TICKERS

# Inicjalizacja aplikacji Flask
flask_app = Flask(__name__)

# Globalne referencje do wątków
fetcher_thread = None
bot_main_thread = None

@flask_app.route('/')
def http_health_check():
    """Prosty endpoint dla health checków App Engine."""
    global fetcher_thread, bot_main_thread
    fetcher_alive = fetcher_thread is not None and fetcher_thread.is_alive()
    bot_alive = bot_main_thread is not None and bot_main_thread.is_alive()

    if fetcher_alive and bot_alive:
        return "Bot worker threads are running.", 200
    elif fetcher_alive:
        return "Bot's fetcher thread is running, but logic thread is NOT.", 503
    elif bot_alive:
        return "Bot's logic thread is running, but fetcher thread is NOT.", 503
    else:
        return "Bot worker threads are NOT running.", 503

def trading_bot_main_loop():
    """Główna pętla logiki trading bota."""
    print("[BOT_LOOP] Pętla logiki bota uruchomiona.")
    startup_delay_passed = False

    # --- POCZĄTEK TESTOWEGO ZAPYTANIA PRZY STARCIE PĘTLI LOGIKI ---
    # To zapytanie wykona się raz przy każdym (re)starcie wątku bot_main_thread
    print("[MAIN_BYBIT_TEST] Próba wykonania testowego zapytania do Bybit przy starcie pętli logiki...")
    test_symbol_main = "BTCUSDT"
    test_url_main = f"{BYBIT_API_URL_V5_TICKERS}?category=linear&symbol={test_symbol_main}"
    test_headers_main = {
        'User-Agent': 'TradingBot/1.0 StartupTest (GCP AppEngine; tradingbotdatabase-c544d)', # Możesz dostosować
        'Accept': 'application/json'
    }
    try:
        print(f"[MAIN_BYBIT_TEST] Wysyłanie GET do: {test_url_main} z nagłówkami: {test_headers_main}")
        response_main_test = requests.get(test_url_main, headers=test_headers_main, timeout=15)
        
        print(f"[MAIN_BYBIT_TEST] Odpowiedź testowa otrzymana. Status: {response_main_test.status_code}")
        print(f"[MAIN_BYBIT_TEST] Nagłówki odpowiedzi testowej (fragment): {str(response_main_test.headers)[:200]}...")
        
        response_main_test_text_snippet = response_main_test.text[:500] if response_main_test.text else "Brak treści tekstowej"
        print(f"[MAIN_BYBIT_TEST] Treść odpowiedzi testowej (fragment): {response_main_test_text_snippet}")

        if 200 <= response_main_test.status_code < 300:
            try:
                response_json_main_test = response_main_test.json()
                print(f"[MAIN_BYBIT_TEST] Treść JSON odpowiedzi testowej (fragment): {str(response_json_main_test)[:500]}...")
            except requests.exceptions.JSONDecodeError:
                print(f"[MAIN_BYBIT_TEST_WARN] Odpowiedź testowa (status {response_main_test.status_code}) nie jest poprawnym JSONem.")
        
    except requests.exceptions.HTTPError as http_err_main:
        status_code_main_test = "Brak response.status_code"
        response_text_main_test_snippet = "Brak response.text"
        # ... (pełniejsza obsługa błędu HTTPError, jak w bot_logic.py, jeśli potrzebujesz) ...
        if http_err_main.response is not None:
            status_code_main_test = str(http_err_main.response.status_code)
            response_text_main_test_snippet = http_err_main.response.text[:500]
        print(f"[MAIN_BYBIT_TEST_ERROR] HTTPError. Status: {status_code_main_test}, Tekst (fragment): {response_text_main_test_snippet}")
    except requests.exceptions.Timeout:
        print(f"[MAIN_BYBIT_TEST_ERROR] Timeout podczas połączenia z {test_url_main}")
    except requests.exceptions.ConnectionError as conn_err_main:
        print(f"[MAIN_BYBIT_TEST_ERROR] ConnectionError dla {test_url_main}: {conn_err_main}")
    except Exception as e_main:
        print(f"[MAIN_BYBIT_TEST_ERROR] Nieoczekiwany błąd ogólny w teście Bybit dla {test_url_main}: {type(e_main).__name__}: {e_main}")
        traceback.print_exc()
    print("[MAIN_BYBIT_TEST] Koniec testowego zapytania do Bybit.")
    # --- KONIEC TESTOWEGO ZAPYTANIA PRZY STARCIE PĘTLI LOGIKI ---

    while True:
        try:
            if not startup_delay_passed and not state_manager.get_all_alert_symbols():
                if not firestore_db_client:
                    print("[BOT_LOOP] Klient Firestore nie jest jeszcze dostępny. Czekam...")
                else:
                    print("[BOT_LOOP] Czekam na pierwsze dane od fetchera...")
                time.sleep(BOT_LOOP_INTERVAL_SECONDS / 2)
                continue
            startup_delay_passed = True

            alert_symbols = set(state_manager.get_all_alert_symbols())
            position_symbols = set(state_manager.get_all_position_symbols())
            symbols_to_monitor = sorted(list(alert_symbols | position_symbols))

            if not symbols_to_monitor:
                # print("[BOT_LOOP_DEBUG] Brak symboli do monitorowania.")
                pass
            
            for symbol in symbols_to_monitor:
                # print(f"[BOT_LOOP_DEBUG] Przetwarzanie symbolu: {symbol}")
                bot_logic.check_new_long_entries(symbol)
                bot_logic.check_new_short_entries(symbol)
                bot_logic.monitor_planned_positions(symbol)
                bot_logic.monitor_opened_positions(symbol)
        except Exception as e_loop:
            print(f"[BOT_LOOP_ERROR] Nieoczekiwany błąd w głównej pętli bota: {type(e_loop).__name__}: {e_loop}")
            traceback.print_exc()
            # Dodaj dłuższy sen w przypadku błędu, aby uniknąć zbyt szybkiego floodowania logów
            time.sleep(BOT_LOOP_INTERVAL_SECONDS * 4) 
        
        time.sleep(BOT_LOOP_INTERVAL_SECONDS)


def start_background_threads():
    """Uruchamia wątki fetchera i logiki bota."""
    global fetcher_thread, bot_main_thread
    print("[MAIN_THREADS] Próba uruchomienia wątków w tle...")

    if not firestore_db_client:
        print("[MAIN_THREADS_CRITICAL] Klient Firestore (db) nie jest dostępny PRZED startem wątków. Wątki nie zostaną uruchomione.")
        return False

    try:
        fetcher_thread = threading.Thread(target=fetcher_loop, name="FetcherThreadFirestore", daemon=True)
        fetcher_thread.start()
        print("[MAIN_THREADS] Wątek fetchera (Firestore) pomyślnie uruchomiony.")

        bot_main_thread = threading.Thread(target=trading_bot_main_loop, name="BotLogicThread", daemon=True)
        bot_main_thread.start()
        print("[MAIN_THREADS] Wątek logiki bota pomyślnie uruchomiony.")
        return True
    except Exception as e_threads:
        print(f"[MAIN_THREADS_ERROR] Błąd podczas uruchamiania wątków: {type(e_threads).__name__}: {e_threads}")
        traceback.print_exc()
        return False

# Ten blok jest wykonywany tylko, gdy skrypt jest uruchamiany bezpośrednio,
# np. `python app/main.py` lokalnie.
# W App Engine z Gunicornem, Gunicorn importuje `flask_app` i uruchamia je,
# więc ten blok __main__ nie jest wykonywany przez Gunicorna.
if __name__ == '__main__':
    print("[MAIN_LOCAL_RUN] Uruchamianie aplikacji lokalnie (nie przez Gunicorn w App Engine)...")
    # Tutaj inicjalizacja Firebase jest obsługiwana w module fetch_from_firestore.py
    if not firestore_db_client:
        print("[MAIN_LOCAL_RUN_CRITICAL] Klient Firestore (db) nie został zainicjowany. Sprawdź konfigurację Firebase.")
        sys.exit(1) # Zakończ, jeśli Firebase nie jest dostępne lokalnie
    else:
        print("[MAIN_LOCAL_RUN] Klient Firestore wydaje się być poprawnie zainicjowany dla uruchomienia lokalnego.")

    if start_background_threads():
        print("[MAIN_LOCAL_RUN] Wątki bota uruchomione dla testu lokalnego.")
        print(f"[MAIN_LOCAL_RUN] Serwer Flask startuje na http://0.0.0.0:8081")
        flask_app.run(host='0.0.0.0', port=8081, debug=False) # debug=False dla testów podobnych do produkcyjnych
    else:
        print("[MAIN_LOCAL_RUN_ERROR] Nie udało się uruchomić wątków bota. Serwer Flask nie startuje.")

# Ten kod poniżej zostanie wykonany, gdy Gunicorn zaimportuje ten moduł w App Engine.
# To jest miejsce, gdzie powinniśmy zainicjować wątki dla środowiska produkcyjnego.
if os.getenv('GAE_ENV', '').startswith('standard'):
    print("[MAIN_APP_ENGINE_INIT] Wykryto środowisko App Engine. Inicjalizacja wątków bota...")
    if not start_background_threads():
        # Jeśli wątki nie wystartują, aplikacja Flask nadal będzie działać i odpowiadać na health checki,
        # ale endpoint '/' może zwracać 503, co zasygnalizuje problem.
        print("[MAIN_APP_ENGINE_INIT_ERROR] Krytyczny błąd: Nie udało się uruchomić wątków bota w tle!")
        # Można by tu podjąć próbę np. zatrzymania aplikacji, ale w App Engine to trudne.
        # Health check zwróci 503, co powinno być widoczne.
else:
    # Ten blok jest na wszelki wypadek, jeśli GAE_ENV nie jest ustawione,
    # ale nie jest to __main__ (co byłoby dziwne).
    if __name__ != '__main__': # Aby uniknąć podwójnego logowania przy lokalnym `python app/main.py`
        print("[MAIN_INIT_WARN] Nie wykryto środowiska App Engine, ale nie jest to też __main__. Sprawdź sposób uruchomienia.")