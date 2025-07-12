# Lokalizacja: /gcp-webhook/main.py
import os
import logging
import hmac
import hashlib
from google.cloud import firestore

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO, format='%(asctime)s - WEBHOOK - %(levelname)s - %(message)s')

# Inicjalizacja zmiennych globalnych
PROJECT_ID = os.environ.get('GCP_PROJECT', 'trading-bot-463318')
DATABASE_NAME = "trading-bot-data"
WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET_TOKEN')
db = None

def get_db_client():
    """Inicjalizuje i buforuje klienta Firestore."""
    global db
    if db is None:
        try:
            logging.info(f"Inicjalizacja klienta Firestore dla projektu '{PROJECT_ID}' i bazy '{DATABASE_NAME}'.")
            db = firestore.Client(project=PROJECT_ID, database=DATABASE_NAME)
            # Testowe zapytanie w celu weryfikacji połączenia
            db.collection('_test_connection_').limit(1).get()
            logging.info("Klient Firestore pomyślnie zainicjalizowany.")
        except Exception as e:
            logging.critical(f"Krytyczny błąd inicjalizacji klienta Firestore: {e}", exc_info=True)
            db = None
            raise
    return db

def firestore_webhook_receiver(request):
    """Główna funkcja (entry point) dla Google Cloud Function z autoryzacją."""
    if request.method != 'POST':
        logging.warning(f"Odrzucono żądanie z niedozwoloną metodą: {request.method}")
        return ('Dozwolone są tylko żądania POST', 405)

    # --- Krok 1: Weryfikacja Sekretu ---
    if not WEBHOOK_SECRET:
        logging.critical("Sekret WEBHOOK_SECRET_TOKEN nie jest skonfigurowany w środowisku Cloud Function!")
        return ("Błąd konfiguracji serwera", 500)

    try:
        alert_data = request.get_json(silent=True)
        if not alert_data or not isinstance(alert_data, dict):
            logging.warning("Otrzymano puste lub nieprawidłowe żądanie JSON.")
            return ("Nieprawidłowe żądanie: Oczekiwano obiektu JSON", 400)

        received_token = alert_data.pop('secret_token', None)
        if not received_token or not hmac.compare_digest(str(received_token), WEBHOOK_SECRET):
            logging.error(f"Nieudana próba autoryzacji. Otrzymany token: '{received_token}'")
            return ("Błąd autoryzacji", 403)
        
        logging.info("Autoryzacja webhooka pomyślna.")

    except Exception as e:
        logging.error(f"Błąd podczas parsowania JSON lub autoryzacji: {e}", exc_info=True)
        return ("Nieprawidłowe żądanie", 400)
    
    # --- Koniec Weryfikacji ---

    try:
        client = get_db_client()
    except Exception:
        return ("Błąd serwera: Klient Firestore niedostępny", 500)

    try:
        required_keys = ["symbol", "directionCode", "entry", "sl", "tp"]
        if not all(key in alert_data for key in required_keys):
            logging.error(f"Brak wymaganych kluczy w alercie: {alert_data}")
            return (f"Brakujące klucze w alercie. Wymagane: {required_keys}", 400)

        logging.info(f"Odebrano poprawny alert dla symbolu: {alert_data.get('symbol')}")
        alert_data['received_at'] = firestore.SERVER_TIMESTAMP
        
        doc_ref = client.collection('alerts').document()
        doc_ref.set(alert_data)
        
        logging.info(f"Pomyślnie zapisano alert. ID dokumentu: {doc_ref.id}")
        return ("Alert zapisany pomyślnie", 201)
    except Exception as e:
        logging.error(f"Błąd przetwarzania alertu po autoryzacji: {e}", exc_info=True)
        return ("Wewnętrzny błąd serwera", 500)