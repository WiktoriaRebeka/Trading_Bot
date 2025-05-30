# trading_bot/app/fetch_from_supabase.py
import psycopg2
import time
import os
from datetime import datetime, timezone
from app import state_manager
from app.constants import (
    # SUPABASE_URL, # Już niepotrzebne dla tego podejścia
    # SUPABASE_TABLE_NAME, # Nadal potrzebne
    # SUPABASE_API_KEY, # Już niepotrzebne dla tego podejścia
    LAST_PROCESSED_TIMESTAMP_FILE,
    FETCH_INTERVAL_SECONDS,
    # Dodaj nowe stałe z .env dla połączenia z bazą danych
    SUPABASE_DB_USER,
    SUPABASE_DB_PASSWORD,
    SUPABASE_DB_HOST,
    SUPABASE_DB_PORT,
    SUPABASE_DB_NAME,
    SUPABASE_TABLE_NAME # Upewnij się, że to jest też w constants.py
)

# HEADERS już niepotrzebne

def load_last_processed_timestamp() -> str:
    # ... (bez zmian) ...
    if os.path.exists(LAST_PROCESSED_TIMESTAMP_FILE):
        try:
            with open(LAST_PROCESSED_TIMESTAMP_FILE, "r") as f:
                ts = f.read().strip()
                datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return ts
        except Exception as e:
            print(f"[FETCHER_WARN] Nie udało się odczytać/sparsować timestampa z pliku: {e}. Używam domyślnego.")
    return "1970-01-01T00:00:00+00:00"

def save_last_processed_timestamp(timestamp_str: str):
    # ... (bez zmian) ...
    try:
        datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        with open(LAST_PROCESSED_TIMESTAMP_FILE, "w") as f:
            f.write(timestamp_str)
    except Exception as e:
        print(f"[FETCHER_ERROR] Nie udało się zapisać timestampu {timestamp_str}: {e}")


def fetch_new_alerts_since(last_ts: str):
    new_alerts_list = []
    max_ts_in_batch = last_ts
    conn = None
    try:
        print(f"[FETCHER_DB] Próba połączenia z bazą danych: {SUPABASE_DB_HOST}")
        conn = psycopg2.connect(
            host=SUPABASE_DB_HOST,
            port=SUPABASE_DB_PORT,
            user=SUPABASE_DB_USER,
            password=SUPABASE_DB_PASSWORD,
            dbname=SUPABASE_DB_NAME,
            sslmode='require' # Supabase wymaga SSL
        )
        cur = conn.cursor() # Domyślnie zwraca krotki, można użyć DictCursor

        # Zmodyfikowane zapytanie SQL
        # Upewnij się, że nazwa tabeli i kolumny 'received_at' jest poprawna
        # PostgreSQL jest wrażliwy na wielkość liter w nazwach kolumn/tabel, jeśli były tworzone w cudzysłowach
        # Jeśli nazwa tabeli to 'alerts' a kolumny 'received_at' i były tworzone bez cudzysłowów, to jest ok.
        query = f"""
            SELECT * FROM {SUPABASE_TABLE_NAME}
            WHERE received_at > %s
            ORDER BY received_at ASC
            LIMIT 200;
        """
        # Konwersja stringa ISO na obiekt datetime dla psycopg2
        last_ts_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))

        print(f"[FETCHER_DB] Pobieranie alertów od: {last_ts_dt}")
        cur.execute(query, (last_ts_dt,))
        
        raw_alerts = cur.fetchall()
        if not raw_alerts:
            # print(f"[FETCHER_DB] Brak nowych alertów od {last_ts_dt}.")
            return new_alerts_list, max_ts_in_batch

        # Przekształć krotki na słowniki (jeśli nie używasz DictCursor)
        # Pobierz nazwy kolumn
        colnames = [desc[0] for desc in cur.description]
        for raw_alert in raw_alerts:
            alert_dict = dict(zip(colnames, raw_alert))
            
            # psycopg2 zwraca obiekty datetime dla timestampów, trzeba je przekonwertować na string ISO z UTC
            if isinstance(alert_dict.get("received_at"), datetime):
                alert_dict["received_at"] = alert_dict["received_at"].replace(tzinfo=timezone.utc).isoformat()
            
            # Podobnie dla innych pól typu timestamp, jeśli istnieją i są potrzebne jako stringi
            # Np. jeśli 'timestamp' z TradingView jest też w bazie jako typ timestamp
            if isinstance(alert_dict.get("timestamp"), datetime):
                 alert_dict["timestamp"] = alert_dict["timestamp"].replace(tzinfo=timezone.utc).isoformat()


            alert_received_at = alert_dict.get("received_at")
            if not alert_received_at:
                print(f"[FETCHER_DB_WARN] Pobrany alert bez 'received_at', pomijanie: {alert_dict.get('id')}")
                continue
            
            new_alerts_list.append(alert_dict)
            if alert_received_at > max_ts_in_batch:
                max_ts_in_batch = alert_received_at
        
        print(f"[FETCHER_DB] Pobrano {len(new_alerts_list)} nowych alertów. Najnowszy timestamp w paczce: {max_ts_in_batch}")

        cur.close()

    except psycopg2.OperationalError as op_err:
        # Sprawdź, czy to problem z rozwiązaniem nazwy hosta
        if "could not translate host name" in str(op_err).lower() or "could not connect to server" in str(op_err).lower():
             print(f"[FETCHER_DB_ERROR] Błąd połączenia z bazą (prawdopodobnie DNS lub sieć): {op_err}")
        else:
             print(f"[FETCHER_DB_ERROR] Błąd operacyjny psycopg2: {op_err}")
    except Exception as e:
        print(f"[FETCHER_DB_ERROR] Inny wyjątek psycopg2: {e}")
    finally:
        if conn:
            conn.close()
    
    return new_alerts_list, max_ts_in_batch

def fetcher_loop():
    # ... (logika pętli bez większych zmian, tylko wywołuje nową fetch_new_alerts_since) ...
    current_last_processed_ts = load_last_processed_timestamp()
    print(f"[FETCHER_DB] Pętla fetchera (DB mode) uruchomiona. Początkowy timestamp: {current_last_processed_ts}")

    while True:
        newly_fetched_alerts, new_max_ts_from_batch = fetch_new_alerts_since(current_last_processed_ts)

        if newly_fetched_alerts:
            print(f"[FETCHER_DB] Przetwarzanie {len(newly_fetched_alerts)} alertów...")
            for alert_data in newly_fetched_alerts:
                state_manager.process_alert(alert_data) 
            
            if new_max_ts_from_batch > current_last_processed_ts:
                current_last_processed_ts = new_max_ts_from_batch
                save_last_processed_timestamp(current_last_processed_ts)
                print(f"[FETCHER_DB] Zaktualizowano ostatni przetworzony timestamp na: {current_last_processed_ts}")
        
        time.sleep(FETCH_INTERVAL_SECONDS)