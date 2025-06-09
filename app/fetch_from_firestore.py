# TRADING_BOT/app/fetch_from_firestore.py
import firebase_admin # Nadal może być potrzebny dla firebase_admin.exceptions lub typów
from firebase_admin import firestore # Dla firestore.FieldFilter, firestore.Query
import time
import os
from datetime import datetime, timezone, timedelta
import traceback # Przeniesione z main.py dla spójności
import sys # Dla logowania do stderr w razie problemów

# Importuj obiekt 'db_client' (lub 'db') z app.main
# LUB przekaż go jako argument do funkcji, które go potrzebują.
# Dla uproszczenia na razie, spróbujmy zaimportować go z app.main
# To stworzy cykliczną zależność importu, jeśli main importuje fetch_from_firestore, a ten próbuje importować z main.
# Lepszym rozwiązaniem jest stworzenie dedykowanego modułu np. app/firebase_setup.py, który inicjuje db
# i jest importowany przez oba moduły.

# === POCZĄTEK ZMIAN DLA CENTRALNEJ INICJALIZACJI ===
# Zakładamy, że db_client będzie zainicjowany w app/main.py i przekazany tutaj
# lub (mniej idealnie) zaimportowany.
# Na razie usuniemy globalną zmienną 'db' z tego modułu, aby uniknąć konfliktów.
# Funkcje będą musiały przyjmować 'db_client' jako argument.

# Globalna zmienna db zostanie usunięta lub zainicjowana z app.main
# db = None 
# --- KONIEC SEKCJI INICJALIZACJI FIREBASE W TYM PLIKU ---

# Zmodyfikujmy funkcje, aby przyjmowały 'db_client' jako argument
# lub polegały na globalnym 'db' zainicjowanym w app.main.py

# Importy modułów Twojego bota
from . import state_manager # Używamy importu relatywnego
from .constants import (    # Używamy importu relatywnego
    FETCH_INTERVAL_SECONDS,
    FIRESTORE_COLLECTION_ALERTS,
    BOT_CONFIG_COLLECTION,
    LAST_FETCH_STATE_DOC_ID,
    LAST_PROCESSED_TS_FIELD
)

# Zmienna 'db' będzie teraz odnosić się do tej zaimportowanej z app.main lub przekazanej
# Jeśli importujesz z app.main, musisz uważać na cykliczne zależności.
# Lepsze rozwiązanie: stwórz app/firebase.py, który inicjuje db,
# i oba moduły importują z app/firebase.py.
# Na razie spróbujmy z globalną zmienną db, którą ustawi app.main.py
# i ten moduł będzie jej używał.

# Aby to zadziałało, 'db' musi być dostępne globalnie w tym module,
# a 'app/main.py' musi je ustawić po inicjalizacji.
# To nie jest najlepsza praktyka. Lepsze jest przekazywanie 'db_client'.

# Dla tego testu, zróbmy tak, że funkcje będą używać globalnej zmiennej 'db',
# a app/main.py ją zainicjuje i ustawi w tym module.
# To jest trochę "hackish", ale pozwoli nam przetestować.

def set_firestore_client(client):
    """Funkcja pomocnicza do ustawienia klienta Firestore z app/main.py"""
    global db
    db = client
    sys.stderr.write(f"[FETCHER_FIRESTORE] Klient Firestore ustawiony z app.main: {db}\n")
    sys.stderr.flush()

def load_last_processed_timestamp() -> str:
    if not db: # Teraz db powinno być ustawione przez app/main.py
        sys.stderr.write("[FETCHER_WARN] Klient Firestore (db) niedostępny w load_last_processed_timestamp. Używam domyślnego.\n")
        sys.stderr.flush()
        return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    # ... reszta funkcji bez zmian ...
    try:
        doc_ref = db.collection(BOT_CONFIG_COLLECTION).document(LAST_FETCH_STATE_DOC_ID)
        doc = doc_ref.get()
        if doc.exists:
            timestamp_obj = doc.get(LAST_PROCESSED_TS_FIELD)
            if timestamp_obj:
                if isinstance(timestamp_obj, datetime):
                    return timestamp_obj.astimezone(timezone.utc).isoformat() # Upewnij się, że jest UTC
                elif isinstance(timestamp_obj, str):
                    datetime.fromisoformat(timestamp_obj.replace("Z", "+00:00")) # Walidacja
                    return timestamp_obj
                else:
                    print(f"[FETCHER_WARN] Oczekiwano datetime lub string dla '{LAST_PROCESSED_TS_FIELD}', otrzymano {type(timestamp_obj)}. Używam domyślnego.")
            else:
                print(f"[FETCHER_INFO] Pole '{LAST_PROCESSED_TS_FIELD}' nie znalezione w dokumencie stanu. Używam domyślnego.")
        else:
            print(f"[FETCHER_INFO] Dokument stanu ({BOT_CONFIG_COLLECTION}/{LAST_FETCH_STATE_DOC_ID}) nie istnieje. Używam domyślnego.")
    except Exception as e:
        print(f"[FETCHER_WARN] Nie udało się odczytać timestampa z Firestore: {e}. Używam domyślnego.")
    
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

def save_last_processed_timestamp(timestamp_str: str):
    if not db:
        sys.stderr.write(f"[FETCHER_ERROR] Klient Firestore (db) niedostępny w save_last_processed_timestamp. Nie można zapisać {timestamp_str}.\n")
        sys.stderr.flush()
        return
    # ... reszta funkcji bez zmian ...
    try:
        ts_dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if ts_dt.tzinfo is None or ts_dt.tzinfo.utcoffset(ts_dt) is None: # Upewnij się, że jest świadomy strefy
            ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        
        doc_ref = db.collection(BOT_CONFIG_COLLECTION).document(LAST_FETCH_STATE_DOC_ID)
        doc_ref.set({
            LAST_PROCESSED_TS_FIELD: ts_dt # Zapisz jako obiekt datetime (Timestamp Firestore)
        }, merge=True)
    except Exception as e:
        print(f"[FETCHER_ERROR] Nie udało się zapisać timestampu {timestamp_str} do Firestore: {e}")


def fetch_new_alerts_since(last_ts_str: str):
    if not db:
        sys.stderr.write("[FETCHER_ERROR] Klient Firestore (db) niedostępny w fetch_new_alerts_since.\n")
        sys.stderr.flush()
        # Zwracamy pustą listę i oryginalny timestamp, żeby nie zepsuć logiki dalej
        return [], last_ts_str 
    # ... reszta funkcji bez zmian ...
    new_alerts_list = []
    max_ts_in_batch_dt = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
    if max_ts_in_batch_dt.tzinfo is None: # Upewnij się, że jest świadomy UTC
        max_ts_in_batch_dt = max_ts_in_batch_dt.replace(tzinfo=timezone.utc)

    try:
        last_ts_dt = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
        if last_ts_dt.tzinfo is None: # Upewnij się, że jest świadomy UTC
            last_ts_dt = last_ts_dt.replace(tzinfo=timezone.utc)
        
        print(f"[FETCHER_FIRESTORE] Pobieranie alertów od: {last_ts_dt.isoformat()}")

        alerts_ref = db.collection(FIRESTORE_COLLECTION_ALERTS)
        query = alerts_ref.where(filter=firestore.FieldFilter('received_at', '>', last_ts_dt)).order_by('received_at', direction=firestore.Query.ASCENDING).limit(200)
        
        docs = query.stream()

        for doc in docs:
            alert_dict = doc.to_dict()
            alert_dict['id'] = doc.id 

            if 'received_at' in alert_dict and isinstance(alert_dict['received_at'], datetime):
                alert_dict['received_at'] = alert_dict['received_at'].astimezone(timezone.utc).isoformat()
            
            if 'timestamp' in alert_dict and isinstance(alert_dict['timestamp'], datetime):
                 alert_dict['timestamp'] = alert_dict['timestamp'].astimezone(timezone.utc).isoformat()

            new_alerts_list.append(alert_dict)
            
            current_alert_ts_str = alert_dict.get("received_at")
            if current_alert_ts_str:
                current_alert_ts_dt = datetime.fromisoformat(current_alert_ts_str.replace("Z", "+00:00"))
                if current_alert_ts_dt.tzinfo is None:
                     current_alert_ts_dt = current_alert_ts_dt.replace(tzinfo=timezone.utc)
                if current_alert_ts_dt > max_ts_in_batch_dt:
                    max_ts_in_batch_dt = current_alert_ts_dt
        
        if new_alerts_list:
            print(f"[FETCHER_FIRESTORE] Pobrano {len(new_alerts_list)} nowych alertów. Najnowszy timestamp w paczce: {max_ts_in_batch_dt.isoformat()}")

    except firebase_admin.exceptions.FirebaseError as fb_err:
        print(f"[FETCHER_FIRESTORE_ERROR] Błąd Firebase: {fb_err}")
    except Exception as e:
        print(f"[FETCHER_FIRESTORE_ERROR] Inny błąd podczas pobierania: {e}")
        traceback.print_exc()

    return new_alerts_list, max_ts_in_batch_dt.isoformat()


def fetcher_loop(): # Ta funkcja będzie używać globalnego 'db'
    if not db: 
        sys.stderr.write("[FETCHER_FIRESTORE_CRITICAL] Klient Firestore (db) nie jest dostępny na początku fetcher_loop. Pętla nie może wystartować.\n")
        sys.stderr.flush()
        return
    if not firebase_admin._apps: # To sprawdzenie może być redundantne, jeśli db jest OK
        sys.stderr.write("[FETCHER_FIRESTORE_ERROR] Firebase Admin SDK nie zostało zainicjowane. Pętla fetchera nie może wystartować.\n")
        sys.stderr.flush()
        return
    # ... reszta funkcji fetcher_loop bez zmian, używając globalnego 'db' ...
    current_last_processed_ts = load_last_processed_timestamp()
    print(f"[FETCHER_FIRESTORE] Pętla fetchera (Firestore) uruchomiona. Początkowy timestamp: {current_last_processed_ts}")
    
    while True: # Pamiętaj, że ta pętla nieskończona jest problematyczna w App Engine, jeśli jest w głównym wątku
        try:
            newly_fetched_alerts, new_max_ts_from_batch_str = fetch_new_alerts_since(current_last_processed_ts)

            if newly_fetched_alerts:
                print(f"[FETCHER_FIRESTORE] Przetwarzanie {len(newly_fetched_alerts)} alertów...")
                for alert_data in newly_fetched_alerts:
                    state_manager.process_alert(alert_data) # Zakładamy, że state_manager jest zaimportowany
                
                if new_max_ts_from_batch_str > current_last_processed_ts:
                    current_last_processed_ts = new_max_ts_from_batch_str
                    save_last_processed_timestamp(current_last_processed_ts)
                    print(f"[FETCHER_FIRESTORE] Zaktualizowano ostatni przetworzony timestamp na: {current_last_processed_ts}")

        except Exception as e:
            print(f"[FETCHER_FIRESTORE_ERROR] Nieoczekiwany błąd w pętli fetchera: {e}")
            traceback.print_exc()

        time.sleep(FETCH_INTERVAL_SECONDS)

sys.stderr.write("[FETCHER_FIRESTORE] Moduł fetch_from_firestore.py (wersja z centralną inicjalizacją db) załadowany.\n")
sys.stderr.flush()