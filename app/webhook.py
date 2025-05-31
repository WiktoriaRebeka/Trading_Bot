# trading_bot/app/webhook.py
from flask import Flask, request, jsonify
from datetime import datetime, timezone
import os
import json # Nadal potrzebne, jeśli TradingView wysyła JSON jako string
import firebase_admin
from firebase_admin import credentials, firestore

# Załaduj stałe, jeśli są potrzebne (np. nazwa kolekcji)
# Upewnij się, że ścieżka do constants.py jest poprawna z perspektywy webhook.py
try:
    from app.constants import FIRESTORE_COLLECTION_ALERTS
except ImportError:
    # Jeśli webhook jest uruchamiany w innym kontekście, gdzie app.constants nie jest dostępne
    # bezpośrednio, zdefiniuj tutaj lub pobierz z env.
    FIRESTORE_COLLECTION_ALERTS = os.getenv("FIRESTORE_COLLECTION_ALERTS", "alerts")


# --- Inicjalizacja Firebase Admin SDK dla webhooka ---
# Ta część jest kluczowa i musi działać poprawnie zarówno lokalnie, jak i na Render.com

# Globalna zmienna dla klienta Firestore
db_firestore_client = None
firebase_app_instance_name = f"webhook-app-{os.getpid()}" # Unikalna nazwa dla instancji

try:
    # Sprawdź, czy aplikacja nie została już zainicjowana pod tą nazwą
    try:
        firebase_admin.get_app(name=firebase_app_instance_name)
    except ValueError: # ValueError: The default Firebase app already exists.
                       # Lub ValueError: Firebase app named "..." does not exist.
                       # Chcemy złapać ten drugi przypadek i zainicjować.
        
        cred_path_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        actual_cred_path = None

        if not cred_path_env:
            print("[WEBHOOK_ERROR] Zmienna środowiskowa GOOGLE_APPLICATION_CREDENTIALS nie jest ustawiona.")
            # W przypadku Render.com, można sprawdzić domyślną ścieżkę do Secret File
            render_secret_file_path = "/etc/secrets/firebase-credentials.json" # Przykładowa ścieżka
            if os.path.exists(render_secret_file_path):
                actual_cred_path = render_secret_file_path
                print(f"[WEBHOOK_INFO] Używam Secret File z Render: {actual_cred_path}")
            else:
                raise ValueError("Brak danych uwierzytelniających Firebase dla webhooka. Ustaw GOOGLE_APPLICATION_CREDENTIALS.")
        else:
            actual_cred_path = cred_path_env

        # Jeśli ścieżka nie jest absolutna, spróbuj zbudować ją względem CWD
        # To może być problematyczne w zależności od tego, jak Render uruchamia aplikację.
        # Najlepiej, jeśli GOOGLE_APPLICATION_CREDENTIALS na Render zawiera absolutną ścieżkę.
        if not os.path.isabs(actual_cred_path):
            # Dla testów lokalnych, zakładając, że webhook.py jest w app/, a klucz w trading_bot/
            local_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # trading_bot/
            potential_local_path = os.path.join(local_base_dir, actual_cred_path)
            if os.path.exists(potential_local_path):
                actual_cred_path = potential_local_path
            # Jeśli nie znaleziono lokalnie, a ścieżka nie jest absolutna, może to być problem na Render
            # chyba że Render sam umieszcza plik w CWD.

        if not os.path.exists(actual_cred_path):
            raise ValueError(f"[WEBHOOK_ERROR] Plik klucza Firebase nie został znaleziony pod ścieżką: {actual_cred_path}")

        cred = credentials.Certificate(actual_cred_path)
        firebase_admin.initialize_app(cred, name=firebase_app_instance_name)
        print(f"[WEBHOOK] Pomyślnie zainicjowano Firebase Admin SDK ({firebase_app_instance_name}). Użyto klucza: {actual_cred_path}")

    # Pobierz klienta bazy danych używając nazwanej aplikacji
    db_firestore_client = firestore.client(app=firebase_admin.get_app(name=firebase_app_instance_name))

except Exception as e:
    print(f"[WEBHOOK_CRITICAL_ERROR] Krytyczny błąd inicjalizacji Firebase Admin SDK dla webhooka: {e}")
    db_firestore_client = None # Ustaw na None, aby sprawdzić później
    import traceback
    traceback.print_exc()
# --- Koniec inicjalizacji Firebase ---


app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook_handler(): # Zmieniono nazwę funkcji, żeby uniknąć konfliktu z modułem 'webhook'
    if not db_firestore_client:
        print("[WEBHOOK_ERROR] Klient Firestore (db_firestore_client) nie jest dostępny. Błąd inicjalizacji SDK?")
        return jsonify({"error": "Server configuration error for database"}), 500

    try:
        data = request.get_json(force=True) 
    except Exception as e:
        print(f"[WEBHOOK_ERROR] Nieprawidłowy JSON: {e} (Dane: {str(request.data)[:200]})")
        return jsonify({"error": "Invalid JSON payload"}), 400

    if not isinstance(data, dict):
        print(f"[WEBHOOK_ERROR] Otrzymane dane nie są słownikiem: {type(data)}")
        return jsonify({"error": "Payload must be a JSON object"}), 400

    # Dodaj serwerowy znacznik czasu UTC
    data["received_at"] = firestore.SERVER_TIMESTAMP

    # Opcjonalnie: Konwertuj 'timestamp' z TradingView na obiekt Timestamp Firestore
    if "timestamp" in data and isinstance(data["timestamp"], str):
        try:
            tv_timestamp_dt = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
            data["timestamp"] = tv_timestamp_dt # Firestore przekonwertuje na swój typ Timestamp
        except ValueError:
            print(f"[WEBHOOK_WARN] Nie udało się sparsować 'timestamp' z TradingView: {data['timestamp']}. Zostawiam jako string.")
            # Możesz zdecydować, czy chcesz go zostawić jako string, czy usunąć
    
    # Usuń puste wartości, jeśli TradingView takowe wysyła i nie chcesz ich w bazie
    # data_to_save = {k: v for k, v in data.items() if v is not None and v != ""}


    try:
        # Dodaj dokument do kolekcji. Firestore sam wygeneruje ID dokumentu.
        # Użyj data lub data_to_save jeśli filtrujesz
        doc_ref = db_firestore_client.collection(FIRESTORE_COLLECTION_ALERTS).add(data)
        
        # print(f"[WEBHOOK_SUCCESS] Zapisano do Firestore: Doc ID {doc_ref[1].id}, Symbol {data.get('symbol')}, Event {data.get('event')}")
        return jsonify({"status": "success", "message": "Alert received and stored in Firestore."}), 201

    except firebase_admin.exceptions.FirebaseError as fb_err:
        print(f"[WEBHOOK_ERROR] Błąd zapisu do Firestore (Firebase): {fb_err}")
        return jsonify({"error": "Failed to store alert in Firestore", "details": str(fb_err)}), 502
    except Exception as e:
        print(f"[WEBHOOK_ERROR] Nieoczekiwany błąd przy zapisie do Firestore: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "An unexpected error occurred"}), 500

if __name__ == "__main__":
    # Upewnij się, że GOOGLE_APPLICATION_CREDENTIALS jest ustawione w .env dla testów lokalnych
    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        print("UWAGA: Zmienna GOOGLE_APPLICATION_CREDENTIALS nie jest ustawiona. Lokalny webhook może nie połączyć się z Firestore.")
    elif not db_firestore_client:
        print("UWAGA: Klient Firestore nie został poprawnie zainicjowany. Lokalny webhook może nie działać.")
    
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)



    #Test dla Render
    