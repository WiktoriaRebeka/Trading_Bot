# trading_bot/app/fetch_from_supabase.py
import requests
import time
import os
from datetime import datetime, timezone
from app import state_manager # Zakładając, że main.py poprawnie konfiguruje sys.path
from app.constants import (
    SUPABASE_URL,
    SUPABASE_TABLE_NAME,
    SUPABASE_API_KEY,
    LAST_PROCESSED_TIMESTAMP_FILE,
    FETCH_INTERVAL_SECONDS
)

HEADERS = {
    "apikey": SUPABASE_API_KEY,
    "Authorization": f"Bearer {SUPABASE_API_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal" # Nie potrzebujemy zwracanych danych przy zapisie, jeśli webhook już to robi
}

def load_last_processed_timestamp() -> str:
    if os.path.exists(LAST_PROCESSED_TIMESTAMP_FILE):
        try:
            with open(LAST_PROCESSED_TIMESTAMP_FILE, "r") as f:
                ts = f.read().strip()
                # Prosta walidacja
                datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return ts
        except Exception as e:
            print(f"[FETCHER_WARN] Nie udało się odczytać/sparsować timestampa z pliku: {e}. Używam domyślnego.")
    return "1970-01-01T00:00:00+00:00" # ISO format z offsetem, bezpieczny start

def save_last_processed_timestamp(timestamp_str: str):
    try:
        # Walidacja przed zapisem
        datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        with open(LAST_PROCESSED_TIMESTAMP_FILE, "w") as f:
            f.write(timestamp_str)
    except Exception as e:
        print(f"[FETCHER_ERROR] Nie udało się zapisać timestampu {timestamp_str}: {e}")

def fetch_new_alerts_since(last_ts: str):
    new_alerts_list = []
    max_ts_in_batch = last_ts
    try:
        # Supabase oczekuje URL-encoded '+' dla strefy czasowej, ale requests to ogarnie
        # Zapytanie o alerty nowsze niż last_ts, sortowane rosnąco
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE_NAME}?select=*&order=received_at.asc&received_at=gt.{last_ts.replace('+', '%2B')}&limit=200"
        
        print(f"[FETCHER] Pobieranie alertów od: {last_ts}")
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status() # Rzuci wyjątek dla błędów HTTP

        data = response.json()
        if not data:
            # print(f"[FETCHER] Brak nowych alertów od {last_ts}.") # Można odkomentować dla częstego logowania
            return new_alerts_list, max_ts_in_batch

        for alert in data:
            alert_received_at = alert.get("received_at")
            if not alert_received_at:
                print(f"[FETCHER_WARN] Pobrany alert bez 'received_at', pomijanie: {alert.get('id')}")
                continue
            
            new_alerts_list.append(alert)
            # Aktualizujemy max_ts_in_batch na najnowszy timestamp z tej paczki
            if alert_received_at > max_ts_in_batch:
                max_ts_in_batch = alert_received_at
        
        print(f"[FETCHER] Pobrano {len(new_alerts_list)} nowych alertów. Najnowszy timestamp w paczce: {max_ts_in_batch}")

    except requests.exceptions.HTTPError as http_err:
        print(f"[FETCHER_ERROR] Błąd HTTP: {http_err.response.status_code} - {http_err.response.text}")
    except requests.exceptions.RequestException as req_err:
        print(f"[FETCHER_ERROR] Błąd sieciowy: {req_err}")
    except Exception as e:
        print(f"[FETCHER_ERROR] Inny wyjątek: {e}")
    
    return new_alerts_list, max_ts_in_batch

def fetcher_loop():
    current_last_processed_ts = load_last_processed_timestamp()
    print(f"[FETCHER] Pętla fetchera uruchomiona. Początkowy timestamp: {current_last_processed_ts}")

    while True:
        newly_fetched_alerts, new_max_ts_from_batch = fetch_new_alerts_since(current_last_processed_ts)

        if newly_fetched_alerts:
            print(f"[FETCHER] Przetwarzanie {len(newly_fetched_alerts)} alertów...")
            for alert_data in newly_fetched_alerts:
                # Funkcja process_alert w state_manager powinna obsługiwać walidację i aktualizację
                state_manager.process_alert(alert_data) 
            
            if new_max_ts_from_batch > current_last_processed_ts:
                current_last_processed_ts = new_max_ts_from_batch
                save_last_processed_timestamp(current_last_processed_ts)
                print(f"[FETCHER] Zaktualizowano ostatni przetworzony timestamp na: {current_last_processed_ts}")
        
        time.sleep(FETCH_INTERVAL_SECONDS)