# trading_bot/app/main.py
import threading
import time
import sys
import os

# Dodaj folder główny do sys.path
current_dir = os.path.dirname(os.path.abspath(__file__)) # app/
parent_dir = os.path.abspath(os.path.join(current_dir, "..")) # trading_bot/
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

# Importy modułów z 'app' dopiero po modyfikacji sys.path
from app import bot_logic, state_manager
from app.fetch_from_firestore import fetcher_loop # <<< ZMIANA TUTAJ
from app.constants import BOT_LOOP_INTERVAL_SECONDS # Usunięto SUPABASE_API_KEY

def trading_bot_main_loop():
    print("[BOT_LOOP] Pętla logiki bota uruchomiona.")
    startup_delay_passed = False

    while True:
        if not startup_delay_passed and not state_manager.get_all_alert_symbols():
            print("[BOT_LOOP] Czekam na pierwsze dane od fetchera...")
            time.sleep(BOT_LOOP_INTERVAL_SECONDS / 2) # Można dać mniejszy interwał na początku
            continue
        startup_delay_passed = True
        
        alert_symbols = set(state_manager.get_all_alert_symbols())
        position_symbols = set(state_manager.get_all_position_symbols())
        symbols_to_monitor = sorted(list(alert_symbols | position_symbols))

        if not symbols_to_monitor:
            # print("[BOT_LOOP] Brak symboli do monitorowania. Czekam...")
            pass
        else:
            # print(f"[BOT_LOOP] Monitorowane symbole: {symbols_to_monitor}") # Może być zbyt gadatliwe
            pass # Zmieniono z print na pass, aby zmniejszyć liczbę logów

        for symbol in symbols_to_monitor:
            bot_logic.check_new_long_entries(symbol)
            bot_logic.check_new_short_entries(symbol)
            bot_logic.monitor_planned_positions(symbol)
            bot_logic.monitor_opened_positions(symbol)
        
        time.sleep(BOT_LOOP_INTERVAL_SECONDS)

if __name__ == "__main__":
    print("[MAIN] Uruchamianie Trading Bota (Firestore mode)...") # <<< ZMIANA W OPISIE

    # Sprawdź, czy GOOGLE_APPLICATION_CREDENTIALS jest ustawione
    google_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not google_creds: # <<< ZMIANA WARUNKU
        print("[MAIN_ERROR] Zmienna środowiskowa GOOGLE_APPLICATION_CREDENTIALS nie jest ustawiona!")
        print("Upewnij się, że plik .env zawiera poprawną ścieżkę do pliku klucza serwisowego Firebase.")
        sys.exit(1)
    else:
        print(f"[MAIN] Znaleziono zmienną GOOGLE_APPLICATION_CREDENTIALS: {google_creds}")
        # Dodatkowa weryfikacja, czy plik faktycznie istnieje
        cred_path_check = google_creds
        # Jeśli ścieżka w .env nie jest absolutna, zbuduj ją względem katalogu projektu
        if not os.path.isabs(cred_path_check):
            # parent_dir to katalog TRADING_BOT/
            cred_path_check = os.path.join(parent_dir, cred_path_check)
        
        if not os.path.exists(cred_path_check):
            print(f"[MAIN_ERROR] Plik klucza serwisowego nie istnieje pod ścieżką: {cred_path_check}")
            print(f"Oczekiwano na podstawie GOOGLE_APPLICATION_CREDENTIALS i katalogu projektu.")
            sys.exit(1)
        else:
            print(f"[MAIN] Plik klucza serwisowego Firebase znaleziony: {cred_path_check}")


    fetcher_thread = threading.Thread(target=fetcher_loop, name="FetcherThreadFirestore", daemon=True) # <<< ZMIANA NAZWY WĄTKU
    # Usunięcie .setName() - użyj argumentu `name=` w konstruktorze Thread
    fetcher_thread.start()
    print("[MAIN] Wątek fetchera (Firestore) uruchomiony.")

    bot_main_thread = threading.Thread(target=trading_bot_main_loop, name="BotLogicThread", daemon=True)
    # Usunięcie .setName()
    bot_main_thread.start()
    print("[MAIN] Wątek logiki bota uruchomiony.")
    
    try:
        while True:
            if not fetcher_thread.is_alive():
                print("[MAIN_ERROR] Wątek fetchera (Firestore) przestał działać!")
                break 
            if not bot_main_thread.is_alive():
                print("[MAIN_ERROR] Wątek logiki bota przestał działać!")
                break
            time.sleep(30)
    except KeyboardInterrupt:
        print("[MAIN] Zatrzymywanie bota przez użytkownika (Ctrl+C)...")
    finally:
        print("[MAIN] Bot zakończył działanie.")