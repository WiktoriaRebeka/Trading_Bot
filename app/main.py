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
from app.fetch_from_firestore import fetcher_loop, db as firestore_db_client # Importuj db, aby sprawdzić inicjalizację
from app.constants import BOT_LOOP_INTERVAL_SECONDS

def trading_bot_main_loop():
    print("[BOT_LOOP] Pętla logiki bota uruchomiona.")
    startup_delay_passed = False

    while True:
        if not startup_delay_passed and not state_manager.get_all_alert_symbols():
            # Czekaj, aż fetcher coś pobierze LUB jeśli db nie jest dostępne, to też czekaj
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

        # Usunięto gadatliwe logi
        # if not symbols_to_monitor:
        #     pass
        # else:
        #     pass

        for symbol in symbols_to_monitor:
            bot_logic.check_new_long_entries(symbol)
            bot_logic.check_new_short_entries(symbol)
            bot_logic.monitor_planned_positions(symbol)
            bot_logic.monitor_opened_positions(symbol)
        
        time.sleep(BOT_LOOP_INTERVAL_SECONDS)

if __name__ == "__main__":
    print("[MAIN] Uruchamianie Trading Bota (Firestore mode)...")

    # --- SEKCJA SPRAWDZANIA GOOGLE_APPLICATION_CREDENTIALS JEST TERAZ OBSŁUGIWANA W fetch_from_firestore.py ---
    # --- Można ją całkowicie usunąć lub zostawić zakomentowaną dla celów historycznych ---
    # print("[MAIN] Inicjalizacja Firebase jest teraz obsługiwana w module fetch_from_firestore.")
    # print("[MAIN] W środowisku App Engine używane są domyślne credentials.")
    # print("[MAIN] Lokalnie, fetch_from_firestore spróbuje użyć GOOGLE_APPLICATION_CREDENTIALS z .env.")

    # Sprawdzenie, czy klient DB został zainicjowany w fetch_from_firestore
    # To ważne, bo bez tego bot nie ma sensu
    if not firestore_db_client:
        print("[MAIN_CRITICAL_ERROR] Klient Firestore (db) nie został zainicjowany w module fetch_from_firestore.")
        print("[MAIN_CRITICAL_ERROR] Bot nie może kontynuować. Sprawdź logi z inicjalizacji Firebase.")
        sys.exit(1)
    else:
        print("[MAIN] Klient Firestore wydaje się być poprawnie zainicjowany.")


    fetcher_thread = threading.Thread(target=fetcher_loop, name="FetcherThreadFirestore", daemon=True)
    fetcher_thread.start()
    print("[MAIN] Wątek fetchera (Firestore) uruchomiony.")

    bot_main_thread = threading.Thread(target=trading_bot_main_loop, name="BotLogicThread", daemon=True)
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