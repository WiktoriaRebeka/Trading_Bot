# app/fetch_from_sqlite.py
import time
import requests
import json
from typing import List
import os

SQLITE_WEBHOOK_URL = "https://ekoenergiadomowa.com/webhook_sqlite.php?format=json"
FETCH_INTERVAL = 60  # seconds
MAX_LINES = 500

LOG_FILE = "alerts_sqlite.jsonl"
last_received_id: int = 0

print("[BOT] Start pobierania alertów z SQLite webhook...")

while True:
    try:
        response = requests.get(SQLITE_WEBHOOK_URL, timeout=10)
        if response.status_code == 200:
            alerts = response.json()
            alerts = sorted(alerts, key=lambda x: x["id"])  # sort by ID rosnąco

            new_alerts = [a for a in alerts if a["id"] > last_received_id]
            if new_alerts:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    for alert in new_alerts:
                        payload = alert.get("payload", alert)
                        data = {
                            "id": alert["id"],
                            "payload": payload,
                            "received_at": alert.get("received_at")
                        }
                        f.write(json.dumps(data) + "\n")

                last_received_id = new_alerts[-1]["id"]
                print(f"[+] Zapisano {len(new_alerts)} nowych alertów.")
            else:
                print("[=] Brak nowych alertów.")

            # trim file to last MAX_LINES
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                if len(lines) > MAX_LINES:
                    with open(LOG_FILE, "w", encoding="utf-8") as f:
                        f.writelines(lines[-MAX_LINES:])

        else:
            print(f"[!] Błąd HTTP: {response.status_code}")

    except Exception as e:
        print(f"[!] Wyjątek podczas pobierania: {e}")

    time.sleep(FETCH_INTERVAL)
