import os
import firebase_admin
from firebase_admin import firestore

# Inicjalizacja, która zadziała automatycznie w Google Cloud
try:
    project_id = os.environ.get("GCP_PROJECT_ID", "trading-bot-463318")
    database_name = "trading-bot-data"
    firebase_admin.initialize_app({'projectId': project_id})
    db = firestore.client(database=database_name)
    print(f"INFO: Inicjalizacja Firebase zakończona. Połączono z bazą '{database_name}'.")
except Exception as e:
    print(f"ERROR: Błąd inicjalizacji Firebase: {e}")
    db = None

def firestore_webhook_receiver(request):
    """Główna funkcja (entry point) dla Google Cloud Function."""
    if db is None:
        print("ERROR: Klient Firestore niedostępny.")
        return ("Błąd serwera: Klient Firestore niedostępny", 500)

    if request.method != 'POST':
        return ('Dozwolone są tylko żądania POST', 405)

    try:
        alert_data = request.get_json(silent=True)
        if not alert_data:
            print("WARNING: Otrzymano puste żądanie.")
            return ("Nieprawidłowe żądanie: Pusty JSON", 400)

        print(f"INFO: Odebrano alert: {alert_data}")
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP
        doc_ref = db.collection('alerts').document()
        doc_ref.set(alert_data)
        print(f"INFO: Pomyślnie zapisano alert. ID: {doc_ref.id}")
        return ("Alert zapisany pomyślnie", 201)
    except Exception as e:
        print(f"ERROR: Błąd przetwarzania alertu: {e}")
        return ("Wewnętrzny błąd serwera", 500)