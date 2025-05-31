# trading_bot/app/fetch_from_firestore.py
import firebase_admin
from firebase_admin import credentials, firestore
import time
import os
from datetime import datetime, timezone, timedelta

from app import state_manager
from app.constants import (
    LAST_PROCESSED_TIMESTAMP_FILE,
    FETCH_INTERVAL_SECONDS,
    FIRESTORE_COLLECTION_ALERTS
)

# Inicjalizacja Firebase Admin SDK
# SDK samo odczyta GOOGLE_APPLICATION_CREDENTIALS z .env (dzięki load_dotenv w constants)
try:
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not cred_path:
        raise ValueError("Zmienna środowiskowa GOOGLE_APPLICATION_CREDENTIALS nie jest ustawiona.")
    
    # Upewnij się, że ścieżka jest absolutna lub relatywna do CWD
    # Jeśli .env ma np. GOOGLE_APPLICATION_CREDENTIALS=serviceAccountKey.json
    # a plik jest w trading_bot/, a uruchamiasz z trading_bot/, to powinno być ok.
    # W przeciwnym razie, zbuduj pełną ścieżkę:
    if not os.path.isabs(cred_path):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # trading_bot/
        cred_path = os.path.join(base_dir, cred_path)

    if not os.path.exists(cred_path):
        raise ValueError(f"Plik klucza serwisowego nie został znaleziony pod ścieżką: {cred_path}")

    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
    print("[FETCHER_FIRESTORE] Pomyślnie zainicjowano Firebase Admin SDK.")
except Exception as e:
    print(f"[FETCHER_FIRESTORE_ERROR] Błąd inicjalizacji Firebase Admin SDK: {e}")
    # Możesz zdecydować o zakończeniu działania, jeśli SDK się nie zainicjuje
    # import sys
    # sys.exit(1)

db = firestore.client()

def load_last_processed_timestamp() -> str:
    if os.path.exists(LAST_PROCESSED_TIMESTAMP_FILE):
        try:
            with open(LAST_PROCESSED_TIMESTAMP_FILE, "r") as f:
                ts = f.read().strip()
            # Walidacja, czy to poprawny ISO timestamp
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return ts
        except Exception as e:
            print(f"[FETCHER_WARN] Nie udało się odczytać/sparsować timestampa z pliku: {e}. Używam domyślnego.")
    # Zwróć timestamp "z przeszłości", aby pobrać wszystko przy pierwszym uruchomieniu
    # Lub datetime.now(timezone.utc) - timedelta(minutes=5) jeśli chcesz tylko ostatnie X minut
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


def save_last_processed_timestamp(timestamp_str: str):
    try:
        # Prosta walidacja
        datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        with open(LAST_PROCESSED_TIMESTAMP_FILE, "w") as f:
            f.write(timestamp_str)
    except Exception as e:
        print(f"[FETCHER_ERROR] Nie udało się zapisać timestampu {timestamp_str}: {e}")


def fetch_new_alerts_since(last_ts_str: str):
    new_alerts_list = []
    max_ts_in_batch_dt = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))

    try:
        # Konwertuj string ISO na obiekt datetime świadomy strefy czasowej dla Firestore
        last_ts_dt = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
        
        print(f"[FETCHER_FIRESTORE] Pobieranie alertów od: {last_ts_dt.isoformat()}")

        alerts_ref = db.collection(FIRESTORE_COLLECTION_ALERTS)
        # Firestore przechowuje timestampy jako własny typ, ale może też porównywać z datetime Pythona
        # Upewnij się, że pole 'received_at' w Firestore jest typu Timestamp
        query = alerts_ref.where('received_at', '>', last_ts_dt).order_by('received_at', direction=firestore.Query.ASCENDING).limit(200)
        
        docs = query.stream()

        for doc in docs:
            alert_dict = doc.to_dict()
            alert_dict['id'] = doc.id # Dodajemy ID dokumentu Firestore, może się przydać

            # Upewnij się, że `received_at` (i `timestamp` jeśli jest) jest stringiem ISO UTC
            # Firestore zwraca timestampy jako obiekty datetime.datetime świadome UTC
            if 'received_at' in alert_dict and isinstance(alert_dict['received_at'], datetime):
                alert_dict['received_at'] = alert_dict['received_at'].isoformat()
            
            if 'timestamp' in alert_dict and isinstance(alert_dict['timestamp'], datetime):
                 alert_dict['timestamp'] = alert_dict['timestamp'].isoformat()

            new_alerts_list.append(alert_dict)
            
            # Aktualizuj max_ts_in_batch
            current_alert_ts_str = alert_dict.get("received_at")
            if current_alert_ts_str:
                current_alert_ts_dt = datetime.fromisoformat(current_alert_ts_str.replace("Z", "+00:00"))
                if current_alert_ts_dt > max_ts_in_batch_dt:
                    max_ts_in_batch_dt = current_alert_ts_dt
        
        if new_alerts_list:
            print(f"[FETCHER_FIRESTORE] Pobrano {len(new_alerts_list)} nowych alertów. Najnowszy timestamp w paczce: {max_ts_in_batch_dt.isoformat()}")

    except firebase_admin.exceptions.FirebaseError as fb_err:
        print(f"[FETCHER_FIRESTORE_ERROR] Błąd Firebase: {fb_err}")
    except Exception as e:
        print(f"[FETCHER_FIRESTORE_ERROR] Inny błąd podczas pobierania: {e}")
        import traceback
        traceback.print_exc()


    return new_alerts_list, max_ts_in_batch_dt.isoformat()


def fetcher_loop():
    # Sprawdź, czy SDK zostało poprawnie zainicjowane
    if not firebase_admin._apps:
        print("[FETCHER_FIRESTORE_ERROR] Firebase Admin SDK nie zostało zainicjowane. Pętla fetchera nie może wystartować.")
        return

    current_last_processed_ts = load_last_processed_timestamp()
    print(f"[FETCHER_FIRESTORE] Pętla fetchera (Firestore) uruchomiona. Początkowy timestamp: {current_last_processed_ts}")
    
    while True:
        try:
            newly_fetched_alerts, new_max_ts_from_batch_str = fetch_new_alerts_since(current_last_processed_ts)

            if newly_fetched_alerts:
                print(f"[FETCHER_FIRESTORE] Przetwarzanie {len(newly_fetched_alerts)} alertów...")
                for alert_data in newly_fetched_alerts:
                    state_manager.process_alert(alert_data) 
                
                # Porównaj stringi ISO
                if new_max_ts_from_batch_str > current_last_processed_ts:
                    current_last_processed_ts = new_max_ts_from_batch_str
                    save_last_processed_timestamp(current_last_processed_ts)
                    print(f"[FETCHER_FIRESTORE] Zaktualizowano ostatni przetworzony timestamp na: {current_last_processed_ts}")
            # else:
                # print(f"[FETCHER_FIRESTORE] Brak nowych alertów od {current_last_processed_ts}")
        except Exception as e:
            print(f"[FETCHER_FIRESTORE_ERROR] Nieoczekiwany błąd w pętli fetchera: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(FETCH_INTERVAL_SECONDS)