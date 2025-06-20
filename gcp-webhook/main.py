from google.cloud import firestore

try:
    # Ten parametr jest kluczowy dla Twojej nazwanej bazy danych
    db = firestore.Client(database="trading-bot-data")
    # Testowe zapytanie sprawdzające połączenie przy starcie
    db.collection('_test_connection_').limit(1).get()
    print(f"INFO: Pomyślnie zainicjowano klienta dla bazy 'trading-bot-data'.")
except Exception as e:
    print(f"ERROR: Krytyczny błąd inicjalizacji klienta Firestore: {e}")
    db = None

def firestore_webhook_receiver(request):
    """Główna funkcja (entry point) dla Google Cloud Function."""
    if db is None:
        print("ERROR: Klient Firestore niedostępny z powodu błędu inicjalizacji.")
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