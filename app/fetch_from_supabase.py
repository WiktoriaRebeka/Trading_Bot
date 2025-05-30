import requests
import json
import time
from app import state_manager
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = "https://cceowvpxutqxnaypnopr.supabase.co"
TABLE_NAME = "alerts"
API_KEY = os.getenv("SUPABASE_ANON_KEY")

HEADERS = {
    "apikey": API_KEY,
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

last_id = 0

def fetch_new_alerts():
    global last_id
    try:
        url = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}?select=*&order=received_at.desc&limit=100"
        response = requests.get(url, headers=HEADERS)
        if response.status_code != 200:
            print(f"[❌] Błąd pobierania z Supabase: {response.status_code}")
            return

        data = response.json()
        new_alerts = []
        for alert in reversed(data):  # od najstarszego
            alert_id = alert.get("id", 0)
            if alert_id > last_id:
                state_manager.update_alert(alert)
                new_alerts.append(alert)
                last_id = max(last_id, alert_id)

        print(f"[BOT] Nowe alerty: {len(new_alerts)}")

    except Exception as e:
        print(f"[❌] Wyjątek podczas fetchu: {e}")

if __name__ == "__main__":
    while True:
        fetch_new_alerts()
        time.sleep(60)
