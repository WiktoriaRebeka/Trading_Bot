# trading_bot/app/main.py
import threading
import time
import sys
import os

# Dodaj folder główny do sys.path - kluczowe dla uruchamiania `python app/main.py`
# z katalogu `trading_bot/`
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, ".."))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Importy modułów z 'app' dopiero po modyfikacji sys.path
from app import bot_logic, state_manager # state_manager też tu potrzebny
from app.fetch_from_supabase import fetcher_loop
from app.constants import SUPABASE_API_KEY, BOT_LOOP_INTERVAL_SECONDS

def trading_bot_main_loop():
    print("[BOT_LOOP] Pętla logiki bota uruchomiona.")
    startup_delay_passed = False # Daj fetcherowi chwilę na start i pobranie danych

    while True:
        if not startup_delay_passed and not state_manager.get_all_alert_symbols():
            print("[BOT_LOOP] Czekam na pierwsze dane od fetchera...")
            time.sleep(BOT_LOOP_INTERVAL_SECONDS) # Czekaj krócej na początku
            continue
        startup_delay_passed = True
        
        # Pobierz symbole, dla których mamy alerty LUB aktywne pozycje
        alert_symbols = set(state_manager.get_all_alert_symbols())
        position_symbols = set(state_manager.get_all_position_symbols())
        symbols_to_monitor = sorted(list(alert_symbols | position_symbols))

        if not symbols_to_monitor:
            # print("[BOT_LOOP] Brak symboli do monitorowania. Czekam...") # Loguj rzadziej
            pass
        else:
            print(f"[BOT_LOOP] Monitorowane symbole: {symbols_to_monitor}")

        for symbol in symbols_to_monitor:
            # print(f"--- Analiza symbolu: {symbol} ---") # Loguj rzadziej
            
            # 1. Sprawdź warunki dla nowych wejść
            bot_logic.check_new_long_entries(symbol)
            bot_logic.check_new_short_entries(symbol)

            # 2. Monitoruj zaplanowane pozycje
            bot_logic.monitor_planned_positions(symbol)

            # 3. Monitoruj otwarte pozycje
            bot_logic.monitor_opened_positions(symbol)
            # print(f"--- Koniec analizy dla: {symbol} ---") # Loguj rzadziej

        # Okresowe podsumowanie stanu (np. co kilka pętli)
        # if time.monotonic() % (BOT_LOOP_INTERVAL_SECONDS * 10) < BOT_LOOP_INTERVAL_SECONDS : # Co ~10 iteracji
        # state_manager.print_state_summary()

        time.sleep(BOT_LOOP_INTERVAL_SECONDS)

if __name__ == "__main__":
    print("[MAIN] Uruchamianie Trading Bota...")
    if not SUPABASE_API_KEY:
        print("[MAIN_ERROR] Klucz SUPABASE_ANON_KEY nie został załadowany. Sprawdź plik .env i app/constants.py.")
        sys.exit(1)
    else:
        print("[MAIN] Klucz Supabase załadowany poprawnie.")

    # Uruchomienie pętli pobierania alertów w osobnym wątku
    fetcher_thread = threading.Thread(target=fetcher_loop, daemon=True)
    fetcher_thread.setName("FetcherThread")
    fetcher_thread.start()
    print("[MAIN] Wątek fetchera uruchomiony.")

    # Uruchomienie głównej pętli logiki bota
    bot_main_thread = threading.Thread(target=trading_bot_main_loop, daemon=True)
    bot_main_thread.setName("BotLogicThread")
    bot_main_thread.start()
    print("[MAIN] Wątek logiki bota uruchomiony.")
    
    try:
        while True:
            # Główny wątek może np. sprawdzać stan innych wątków
            if not fetcher_thread.is_alive():
                print("[MAIN_ERROR] Wątek fetchera przestał działać!")
                # Można próbować restartować lub zakończyć program
                break 
            if not bot_main_thread.is_alive():
                print("[MAIN_ERROR] Wątek logiki bota przestał działać!")
                break
            time.sleep(30) # Sprawdzaj co 30 sekund
    except KeyboardInterrupt:
        print("[MAIN] Zatrzymywanie bota przez użytkownika (Ctrl+C)...")
    finally:
        print("[MAIN] Bot zakończył działanie.")
        # Tutaj można dodać logikę czyszczenia, jeśli potrzebna