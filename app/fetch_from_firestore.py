# trading_bot/app/fetch_from_firestore.py
import firebase_admin
from firebase_admin import credentials, firestore # credentials potrzebne do fallbacku lokalnego
import time
import os
from datetime import datetime, timezone, timedelta

from app import state_manager
from app.constants import (
    # LAST_PROCESSED_TIMESTAMP_FILE, # Usunięte, nie używamy już pliku
    FETCH_INTERVAL_SECONDS,
    FIRESTORE_COLLECTION_ALERTS
)

# Stałe dla przechowywania stanu w Firestore
BOT_CONFIG_COLLECTION = "bot_config"  # Możesz przenieść do app/constants.py
LAST_FETCH_STATE_DOC_ID = "last_fetch_state" # Możesz przenieść do app/constants.py
LAST_PROCESSED_TS_FIELD = "last_processed_firestore_timestamp" # Możesz przenieść do app/constants.py

db = None # Zdefiniuj db na początku, aby było dostępne globalnie w module

# --- POCZĄTEK SEKCJI INICJALIZACJI FIREBASE ---
IS_APP_ENGINE_ENVIRONMENT = os.getenv('GAE_ENV', '').startswith('standard')

if IS_APP_ENGINE_ENVIRONMENT:
    print("[FETCHER_FIRESTORE] Wykryto środowisko App Engine. Próba inicjalizacji z domyślnymi credentials.")
    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        db = firestore.client()
        print("[FETCHER_FIRESTORE] Pomyślnie zainicjowano Firebase Admin SDK w App Engine.")
    except Exception as e:
        print(f"[FETCHER_FIRESTORE_ERROR_APP_ENGINE] Błąd inicjalizacji Firebase Admin SDK w App Engine: {e}")
        # W App Engine błąd tutaj jest krytyczny, instancja może nie działać poprawnie.
else:
    print("[FETCHER_FIRESTORE] Nie wykryto środowiska App Engine. Próba inicjalizacji lokalnej z GOOGLE_APPLICATION_CREDENTIALS.")
    try:
        cred_path_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not cred_path_env:
            raise ValueError("LOKALNIE: Zmienna środowiskowa GOOGLE_APPLICATION_CREDENTIALS nie jest ustawiona.")
        
        actual_cred_path = cred_path_env
        if not os.path.isabs(actual_cred_path):
            # Dla testów lokalnych, zakładamy, że .env jest w głównym katalogu projektu (TRADING_BOT)
            # a ścieżka w .env jest relatywna do tego katalogu
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # TRADING_BOT/
            actual_cred_path = os.path.join(base_dir, actual_cred_path)

        if not os.path.exists(actual_cred_path):
            raise ValueError(f"LOKALNIE: Plik klucza serwisowego nie został znaleziony pod ścieżką: {actual_cred_path}")

        cred = credentials.Certificate(actual_cred_path)
        if not firebase_admin._apps: # Tylko jeśli nie zainicjowano wcześniej (np. przez App Engine path, choć tu nie powinno)
            firebase_admin.initialize_app(cred)
        else: # Jeśli _apps istnieje, ale my jesteśmy w ścieżce lokalnej, mogło być zainicjowane bez credentials
            # To jest mało prawdopodobny scenariusz, ale dla bezpieczeństwa
            try:
                firebase_admin.get_app() # Sprawdź, czy domyślna aplikacja ma credentials
            except ValueError: # Jeśli domyślna aplikacja nie ma credentials, a my mamy
                 print("[FETCHER_FIRESTORE_WARN] Domyślna aplikacja Firebase istnieje, ale może nie mieć credentials. Próba użycia podanej ścieżki.")
                 # Można rozważyć inicjalizację z unikalną nazwą aplikacji, ale to komplikuje
                 # Na razie zakładamy, że jeśli _apps istnieje, to jest OK lub App Engine path zawiodło.

        db = firestore.client()
        print(f"[FETCHER_FIRESTORE] Pomyślnie zainicjowano Firebase Admin SDK LOKALNIE z kluczem: {actual_cred_path}")
    except Exception as e:
        print(f"[FETCHER_FIRESTORE_ERROR_LOCAL] Błąd inicjalizacji Firebase Admin SDK LOKALNIE: {e}")

if not db:
    print("[FETCHER_FIRESTORE_CRITICAL] Klient Firestore (db) nie został zainicjowany! Fetcher nie będzie działać poprawnie.")
# --- KONIEC SEKCJI INICJALIZACJI FIREBASE ---


def load_last_processed_timestamp() -> str:
    """Wczytuje ostatni przetworzony timestamp z dedykowanego dokumentu w Firestore."""
    if not db:
        print("[FETCHER_WARN] Klient Firestore niedostępny w load_last_processed_timestamp. Używam domyślnego.")
        return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        
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
    """Zapisuje ostatni przetworzony timestamp do dedykowanego dokumentu w Firestore."""
    if not db:
        print(f"[FETCHER_ERROR] Klient Firestore niedostępny w save_last_processed_timestamp. Nie można zapisać {timestamp_str}.")
        return
        
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
    new_alerts_list = []
    max_ts_in_batch_dt = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
    if max_ts_in_batch_dt.tzinfo is None: # Upewnij się, że jest świadomy UTC
        max_ts_in_batch_dt = max_ts_in_batch_dt.replace(tzinfo=timezone.utc)

    if not db:
        print("[FETCHER_ERROR] Klient Firestore niedostępny w fetch_new_alerts_since.")
        return new_alerts_list, max_ts_in_batch_dt.isoformat()

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
        import traceback
        traceback.print_exc()

    return new_alerts_list, max_ts_in_batch_dt.isoformat()


def fetcher_loop():
    if not db: # Sprawdź, czy db zostało poprawnie zainicjowane na poziomie modułu
        print("[FETCHER_FIRESTORE_CRITICAL] Klient Firestore (db) nie jest dostępny na początku fetcher_loop. Pętla nie może wystartować.")
        return
    # Sprawdzenie _apps jest bardziej ogólne, ale db jest tym, czego bezpośrednio używamy
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
                
                if new_max_ts_from_batch_str > current_last_processed_ts:
                    current_last_processed_ts = new_max_ts_from_batch_str
                    save_last_processed_timestamp(current_last_processed_ts)
                    print(f"[FETCHER_FIRESTORE] Zaktualizowano ostatni przetworzony timestamp na: {current_last_processed_ts}")

        except Exception as e:
            print(f"[FETCHER_FIRESTORE_ERROR] Nieoczekiwany błąd w pętli fetchera: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(FETCH_INTERVAL_SECONDS)