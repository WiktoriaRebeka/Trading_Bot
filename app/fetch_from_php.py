# app/fetch_from_php.py
import time
import requests
import hashlib
import json

PHP_ALERTS_URL = "https://ekoenergiadomowa.com/alerts_log.txt"
LOCAL_LOG_FILE = "alerts_log.jsonl"
FETCH_INTERVAL = 60  # seconds

last_hash = None

def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def append_new_lines(content: str):
    try:
        with open(LOCAL_LOG_FILE, "a", encoding="utf-8") as f:
            for line in content.strip().splitlines():
                f.write(json.dumps({
                    "raw": line,
                    "received_at": time.strftime("%Y-%m-%d %H:%M:%S")
                }) + "\n")
    except Exception as e:
        print(f"[BŁĄD] Zapis alertów: {e}")

print("[BOT] Start pobierania alertów z webhook.php...")
while True:
    try:
        response = requests.get(PHP_ALERTS_URL, timeout=10)
        if response.status_code == 200:
            content = response.text.strip()
            current_hash = hash_content(content)

            if current_hash != last_hash:
                append_new_lines(content)
                print("[✅] Nowe alerty zapisane.")
                last_hash = current_hash
            else:
                print("[⏳] Brak nowych alertów.")
        else:
            print(f"[⛔] Status HTTP: {response.status_code}")
    except Exception as e:
        print(f"[BŁĄD] Pobieranie nieudane: {e}")

    time.sleep(FETCH_INTERVAL)
