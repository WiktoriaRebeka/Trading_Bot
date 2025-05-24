# app/main.py

import threading
import time
import subprocess
import sys
from app import bot_logic
from app.state_manager import data_store

# 🔁 Pętla BOTa – sprawdza LONG i SHORT dla każdego symbolu z alertami
def bot_loop():
    while True:
        symbols_to_monitor = list(data_store.keys())  # tylko symbole z alertami

        for symbol in symbols_to_monitor:
            print(f"[🔎] Analiza symbolu: {symbol}")
            bot_logic.check_long_entry(symbol)
            bot_logic.check_short_entry(symbol)

        time.sleep(15)

# ▶️ Uruchamia pobieranie alertów z webhook_sqlite.php
def run_fetch_loop():
    subprocess.run([sys.executable, "app/fetch_from_sqlite.py"])

if __name__ == "__main__":
    threading.Thread(target=run_fetch_loop, daemon=True).start()
    bot_loop()
