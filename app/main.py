# TRADING_BOT/app/main.py
import threading
import time
import sys
import os
from flask import Flask # Dodaj Flask

# Twoje obecne importy
current_dir = os.path.dirname(os.path.abspath(__file__)) 
parent_dir = os.path.abspath(os.path.join(current_dir, "..")) 
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from app import bot_logic, state_manager # Załóżmy, że te importy są OK
from app.fetch_from_firestore import fetcher_loop, db as firestore_db_client 
from app.constants import BOT_LOOP_INTERVAL_SECONDS

# Utwórz instancję aplikacji Flask
flask_app = Flask(__name__)

@flask_app.route('/')
def http_health_check():
    # Prosty endpoint, który App Engine może sprawdzać
    # Możesz tu dodać logikę sprawdzania, czy wątki bota żyją, jeśli chcesz
    if fetcher_thread is not None and fetcher_thread.is_alive() and \
       bot_main_thread is not None and bot_main_thread.is_alive():
        return "Bot worker is running", 200
    else:
        return "Bot worker is NOT running", 503 # Zwróć błąd, jeśli wątki nie działają

# Globalne referencje do wątków, aby móc je sprawdzić w health checku
fetcher_thread = None
bot_main_thread = None

def trading_bot_main_loop():
    # ... (Twoja obecna logika trading_bot_main_loop, w tym testowe zapytanie do Bybit)
    # Upewnij się, że ta funkcja nie kończy się sama z siebie, tylko działa w pętli
    print("[BOT_LOOP] Pętla logiki bota uruchomiona.")
    startup_delay_passed = False

    # --- POCZĄTEK TESTOWEGO ZAPYTANIA PRZY STARCIE ---
    # ... (kod testowego zapytania, który przygotowaliśmy wcześniej) ...
    # --- KONIEC TESTOWEGO ZAPYTANIA PRZY STARCIE ---

    while True:
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

        for symbol in symbols_to_monitor:
            bot_logic.check_new_long_entries(symbol)
            bot_logic.check_new_short_entries(symbol)
            bot_logic.monitor_planned_positions(symbol)
            bot_logic.monitor_opened_positions(symbol)
        
        time.sleep(BOT_LOOP_INTERVAL_SECONDS)


def start_background_threads():
    global fetcher_thread, bot_main_thread
    print("[MAIN] Uruchamianie wątków w tle...")

    if not firestore_db_client:
        print("[MAIN_CRITICAL_ERROR] Klient Firestore nie jest dostępny PRZED startem wątków.")
        # Można by tu zgłosić wyjątek, aby Flask nie wystartował, jeśli DB jest krytyczne
        return False # Wskazuje na błąd

    fetcher_thread = threading.Thread(target=fetcher_loop, name="FetcherThreadFirestore", daemon=True)
    fetcher_thread.start()
    print("[MAIN] Wątek fetchera (Firestore) uruchomiony.")

    bot_main_thread = threading.Thread(target=trading_bot_main_loop, name="BotLogicThread", daemon=True)
    bot_main_thread.start()
    print("[MAIN] Wątek logiki bota uruchomiony.")
    return True # Wskazuje na sukces

# Uruchom wątki w tle tylko raz, gdy aplikacja Flask startuje
# Ten kod (__name__ == '__main__') nie zostanie wykonany w App Engine, gdy używany jest Gunicorn.
# Gunicorn importuje obiekt 'flask_app'.
# Dlatego musimy znaleźć inny sposób na uruchomienie wątków przy starcie Gunicorna.
# Jednym ze sposobów jest użycie hooka Gunicorna, ale to komplikuje app.yaml.
# Prostsze dla App Engine: uruchom wątki zaraz po definicji aplikacji Flask.

if os.getenv('GAE_ENV', '').startswith('standard'):
    # Ten blok zostanie wykonany tylko w środowisku App Engine
    print("[MAIN_APP_ENGINE_INIT] Wykryto środowisko App Engine.")
    if not start_background_threads():
        print("[MAIN_APP_ENGINE_INIT_ERROR] Nie udało się uruchomić wątków w tle. Aplikacja może nie działać poprawnie.")
        # Możesz tu dodać logikę, aby aplikacja Flask zwróciła błąd, jeśli wątki są krytyczne
else:
    # Dla uruchomienia lokalnego z `python app/main.py` (jeśli chcesz tak testować)
    print("[MAIN_LOCAL_INIT] Uruchamianie lokalne (nie przez Gunicorn).")
    # start_background_threads() # Możesz to odkomentować, jeśli chcesz testować wątki lokalnie

# Jeśli uruchamiasz lokalnie przez `python app/main.py` i chcesz też serwer Flask
if __name__ == "__main__":
    print("[MAIN] Uruchamianie Trading Bota (Firestore mode) z serwerem Flask lokalnie...")
    if not firestore_db_client:
        print("[MAIN_CRITICAL_ERROR] Klient Firestore (db) nie został zainicjowany.")
        sys.exit(1)
    else:
        print("[MAIN] Klient Firestore wydaje się być poprawnie zainicjowany.")
    
    if start_background_threads():
        print("[MAIN] Wątki w tle uruchomione dla testu lokalnego.")
        flask_app.run(host='0.0.0.0', port=8081) # Użyj innego portu niż 8080, jeśli Gunicorn go używa
    else:
        print("[MAIN_ERROR] Nie udało się uruchomić wątków w tle. Serwer Flask nie startuje.")