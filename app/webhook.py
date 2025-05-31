# trading_bot/app/webhook.py
from flask import Flask, request, jsonify
from datetime import datetime, timezone
import os
import json # Nadal potrzebne, jeśli TradingView wysyła JSON jako string
import firebase_admin
from firebase_admin import credentials, firestore
import uuid # Dodane do generowania unikalnego ID dla logowania, jeśli potrzebne

# Załaduj stałe, jeśli są potrzebne (np. nazwa kolekcji)
try:
    from app.constants import FIRESTORE_COLLECTION_ALERTS
except ImportError:
    FIRESTORE_COLLECTION_ALERTS = os.getenv("FIRESTORE_COLLECTION_ALERTS", "alerts")
    print(f"!!!!!!!!!!!! WEBHOOK: Nie udało się zaimportować FIRESTORE_COLLECTION_ALERTS z app.constants, używam wartości z env lub domyślnej: {FIRESTORE_COLLECTION_ALERTS} !!!!!!!!!!!!")


# --- Inicjalizacja Firebase Admin SDK dla webhooka ---
db_firestore_client = None
firebase_app_instance_name = f"webhook-app-{os.getpid()}-{uuid.uuid4().hex[:6]}" # Jeszcze bardziej unikalna nazwa

try:
    print(f"!!!!!!!!!!!! WEBHOOK: Rozpoczynam inicjalizację Firebase SDK dla instancji: {firebase_app_instance_name} !!!!!!!!!!!!")
    # Sprawdź, czy aplikacja nie została już zainicjowana pod tą nazwą
    try:
        current_app = firebase_admin.get_app(name=firebase_app_instance_name)
        print(f"!!!!!!!!!!!! WEBHOOK: Firebase SDK ({firebase_app_instance_name}) już zainicjowane. !!!!!!!!!!!!")
    except ValueError: # Oczekujemy tego błędu, jeśli aplikacja nie jest jeszcze zainicjowana
        print(f"!!!!!!!!!!!! WEBHOOK: Firebase SDK ({firebase_app_instance_name}) nie było zainicjowane, próba inicjalizacji. !!!!!!!!!!!!")
        cred_path_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        actual_cred_path = None

        if not cred_path_env:
            print("!!!!!!!!!!!! WEBHOOK_ERROR: Zmienna GOOGLE_APPLICATION_CREDENTIALS nie jest ustawiona. Próbuję ścieżki Render Secret File. !!!!!!!!!!!!")
            render_secret_file_path = "/etc/secrets/firebase_key.json" # Zmień 'firebase_key.json' jeśli nazwa Twojego Secret File jest inna
            if os.path.exists(render_secret_file_path):
                actual_cred_path = render_secret_file_path
                print(f"!!!!!!!!!!!! WEBHOOK_INFO: Używam Secret File z Render: {actual_cred_path} !!!!!!!!!!!!")
            else:
                print(f"!!!!!!!!!!!! WEBHOOK_ERROR: Plik Secret File {render_secret_file_path} nie znaleziony. Błąd krytyczny inicjalizacji. !!!!!!!!!!!!")
                raise ValueError("Brak danych uwierzytelniających Firebase dla webhooka. Ustaw GOOGLE_APPLICATION_CREDENTIALS lub sprawdź Secret File.")
        else:
            actual_cred_path = cred_path_env
            print(f"!!!!!!!!!!!! WEBHOOK_INFO: Znaleziono GOOGLE_APPLICATION_CREDENTIALS: {actual_cred_path} !!!!!!!!!!!!")

        if not os.path.isabs(actual_cred_path):
            print(f"!!!!!!!!!!!! WEBHOOK_INFO: Ścieżka cred_path '{actual_cred_path}' nie jest absolutna. Próba rozwiązania względem lokalizacji. !!!!!!!!!!!!")
            # Dla Render, jeśli ścieżka to tylko nazwa pliku, zakładamy, że jest w CWD lub Render ją udostępnia
            # Dla testów lokalnych:
            local_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            potential_local_path = os.path.join(local_base_dir, actual_cred_path)
            if os.path.exists(potential_local_path):
                actual_cred_path = potential_local_path
                print(f"!!!!!!!!!!!! WEBHOOK_INFO: Rozwiązano lokalną ścieżkę do: {actual_cred_path} !!!!!!!!!!!!")
            else:
                 print(f"!!!!!!!!!!!! WEBHOOK_WARN: Nie znaleziono pliku pod lokalnie rozwiązaną ścieżką: {potential_local_path}. Polegam na tym, że Render udostępni '{actual_cred_path}' w CWD. !!!!!!!!!!!!")


        if not os.path.exists(actual_cred_path):
            print(f"!!!!!!!!!!!! WEBHOOK_ERROR: Plik klucza Firebase nie został znaleziony pod ostateczną ścieżką: {actual_cred_path} !!!!!!!!!!!!")
            raise ValueError(f"Plik klucza Firebase nie został znaleziony pod ścieżką: {actual_cred_path}")

        cred = credentials.Certificate(actual_cred_path)
        firebase_admin.initialize_app(cred, name=firebase_app_instance_name)
        print(f"!!!!!!!!!!!! WEBHOOK: Pomyślnie zainicjowano Firebase Admin SDK ({firebase_app_instance_name}). Użyto klucza: {actual_cred_path} !!!!!!!!!!!!")

    db_firestore_client = firestore.client(app=firebase_admin.get_app(name=firebase_app_instance_name))
    print(f"!!!!!!!!!!!! WEBHOOK: Klient Firestore dla ({firebase_app_instance_name}) uzyskany. !!!!!!!!!!!!")

except Exception as e:
    print(f"!!!!!!!!!!!! WEBHOOK_CRITICAL_ERROR: Krytyczny błąd podczas inicjalizacji Firebase Admin SDK dla webhooka: {e} !!!!!!!!!!!!")
    db_firestore_client = None
    import traceback
    traceback.print_exc()
# --- Koniec inicjalizacji Firebase ---


app = Flask(__name__)
print(f"!!!!!!!!!!!! WEBHOOK: Aplikacja Flask '{__name__}' utworzona. !!!!!!!!!!!!")


@app.route("/webhook", methods=["POST"])
def webhook_handler():
    print(f"!!!!!!!!!!!! WEBHOOK: Odebrano żądanie na /webhook metodą {request.method} !!!!!!!!!!!!")
    if not db_firestore_client:
        print("!!!!!!!!!!!! WEBHOOK_ERROR: Klient Firestore (db_firestore_client) nie jest dostępny w handlerze. Błąd inicjalizacji SDK? !!!!!!!!!!!!")
        return jsonify({"error": "Server configuration error for database"}), 500

    raw_request_data = request.data # Pobierz surowe dane na wszelki wypadek
    try:
        data = request.get_json(force=True)
        print(f"!!!!!!!!!!!! WEBHOOK: Odebrane i sparsowane dane JSON: {json.dumps(data, indent=2)} !!!!!!!!!!!!")
    except Exception as e:
        print(f"!!!!!!!!!!!! WEBHOOK_ERROR: Nieprawidłowy JSON. Błąd: {e}. Surowe dane: {raw_request_data[:500]} !!!!!!!!!!!!")
        return jsonify({"error": "Invalid JSON payload"}), 400

    if not isinstance(data, dict):
        print(f"!!!!!!!!!!!! WEBHOOK_ERROR: Otrzymane dane po sparsowaniu nie są słownikiem: {type(data)}. Dane: {data} !!!!!!!!!!!!")
        return jsonify({"error": "Payload must be a JSON object"}), 400

    # Przygotowanie danych do zapisu
    data_to_save = data.copy() # Pracuj na kopii
    data_to_save["received_at"] = firestore.SERVER_TIMESTAMP
    print(f"!!!!!!!!!!!! WEBHOOK: Dodano received_at (SERVER_TIMESTAMP) do danych. !!!!!!!!!!!!")

    if "timestamp" in data_to_save and isinstance(data_to_save["timestamp"], str):
        try:
            tv_timestamp_dt = datetime.fromisoformat(data_to_save["timestamp"].replace("Z", "+00:00"))
            data_to_save["timestamp"] = tv_timestamp_dt
            print(f"!!!!!!!!!!!! WEBHOOK: Skonwertowano 'timestamp' z TV na obiekt datetime: {tv_timestamp_dt} !!!!!!!!!!!!")
        except ValueError:
            print(f"!!!!!!!!!!!! WEBHOOK_WARN: Nie udało się sparsować 'timestamp' z TradingView: {data_to_save['timestamp']}. Zostawiam jako string. !!!!!!!!!!!!")
    
    print(f"!!!!!!!!!!!! WEBHOOK: Dane przygotowane do zapisu do Firestore: {json.dumps(data_to_save, default=str, indent=2)} !!!!!!!!!!!!") # default=str dla obiektów datetime

    try:
        doc_ref_tuple = db_firestore_client.collection(FIRESTORE_COLLECTION_ALERTS).add(data_to_save)
        doc_id_created = doc_ref_tuple[1].id
        print(f"!!!!!!!!!!!! WEBHOOK_SUCCESS: Zapisano dokument do Firestore. ID dokumentu: {doc_id_created} !!!!!!!!!!!!")
        return jsonify({"status": "success", "message": "Alert received and stored in Firestore.", "doc_id": doc_id_created}), 201

    except firebase_admin.exceptions.FirebaseError as fb_err:
        print(f"!!!!!!!!!!!! WEBHOOK_ERROR_FIREBASE: Błąd zapisu do Firestore. Błąd Firebase: {fb_err}. Dane, które próbowano zapisać: {json.dumps(data_to_save, default=str, indent=2)} !!!!!!!!!!!!")
        return jsonify({"error": "Failed to store alert in Firestore", "details": str(fb_err)}), 502
    except Exception as e:
        print(f"!!!!!!!!!!!! WEBHOOK_ERROR_UNEXPECTED: Nieoczekiwany błąd przy zapisie do Firestore. Błąd: {e}. Dane, które próbowano zapisać: {json.dumps(data_to_save, default=str, indent=2)} !!!!!!!!!!!!")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "An unexpected error occurred"}), 500

if __name__ == "__main__":
    print("!!!!!!!!!!!! WEBHOOK: Uruchamianie aplikacji Flask lokalnie (if __name__ == '__main__'). !!!!!!!!!!!!")
    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        print("!!!!!!!!!!!! WEBHOOK_WARN_LOCAL: Zmienna GOOGLE_APPLICATION_CREDENTIALS nie jest ustawiona. Lokalny webhook może nie połączyć się z Firestore. !!!!!!!!!!!!")
    elif not db_firestore_client:
        print("!!!!!!!!!!!! WEBHOOK_WARN_LOCAL: Klient Firestore nie został poprawnie zainicjowany. Lokalny webhook może nie działać. !!!!!!!!!!!!")
    
    port = int(os.environ.get("PORT", 8000))
    print(f"!!!!!!!!!!!! WEBHOOK_LOCAL: Uruchamianie na hoście 0.0.0.0 i porcie {port} !!!!!!!!!!!!")
    app.run(host="0.0.0.0", port=port, debug=True)