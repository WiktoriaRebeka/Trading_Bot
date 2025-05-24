# app/fetch_from_sqlite.py
import time
import requests
import json
from typing import List

SQLITE_WEBHOOK_URL = "https://ekoenergiadomowa.com/webhook_sqlite.php?format=json"
FETCH_INTERVAL = 60  # seconds

LOG_FILE = "alerts_sqlite.jsonl"
last_received_ids: List[int] = []

print("[BOT] Start pobierania alertów z SQLite webhook...")

while True:
    try:
        response = requests.get(SQLITE_WEBHOOK_URL, timeout=10)
        if response.status_code == 200:
            alerts = response.json()
            new_alerts = [a for a in alerts if a["id"] not in last_received_ids]

            if new_alerts:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    for alert in new_alerts:
                        payload = alert.get("payload", alert)  # fallback na surowy JSON

                        # Sprawdź czy dane są kompletne
                        symbol = payload.get("symbol") or payload.get("ticker")
                        event = payload.get("event") or payload.get("type")

                        if not symbol or not event:
                            print(f"[⚠️] Pominięto alert bez symbolu lub eventu: {payload}")
                            continue

                        data = {
                            "id": alert["id"],
                            "payload": payload,
                            "received_at": alert.get("received_at")
                        }
                        f.write(json.dumps(data) + "\n")

                last_received_ids = [a["id"] for a in alerts[-10:]]  # remember last 10 IDs
                print(f"[+] Zapisano {len(new_alerts)} nowych alertów.")
            else:
                print("[=] Brak nowych alertów.")
        else:
            print(f"[!] Błąd HTTP: {response.status_code}")

    except Exception as e:
        print(f"[!] Wyjątek podczas pobierania: {e}")

    time.sleep(FETCH_INTERVAL)