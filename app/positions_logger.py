# trading_bot/app/positions_logger.py
import json # Może być przydatny do fallback logów lub debugowania
import os # Potrzebny do os.getenv()
from datetime import datetime, timezone
from typing import Optional, Literal

# Importuj klienta 'db' zainicjowanego w app.fetch_from_firestore
try:
    from app.fetch_from_firestore import db as firestore_db_client
    # Zaimportuj również sam moduł firestore, aby mieć dostęp do firestore.SERVER_TIMESTAMP
    from firebase_admin import firestore as firebase_firestore_module
except ImportError:
    print("[POS_LOGGER_CRITICAL] Nie można zaimportować klienta Firestore 'db' lub modułu 'firebase_admin.firestore'. Logowanie pozycji do Firestore nie będzie działać.")
    firestore_db_client = None
    firebase_firestore_module = None

# Nazwa kolekcji w Firestore dla logów pozycji
# Możesz ją przenieść do app/constants.py jeśli chcesz
POSITIONS_COLLECTION_FIRESTORE = "trading_positions"

PositionStatus = Literal["planned", "opened", "closed", "cancelled"]

def get_firestore_server_timestamp_or_fallback():
    """Zwraca firestore.SERVER_TIMESTAMP jeśli dostępne, w przeciwnym razie czas klienta UTC."""
    if firestore_db_client and firebase_firestore_module:
        return firebase_firestore_module.SERVER_TIMESTAMP
    else:
        print("[POS_LOGGER_WARN] firestore.SERVER_TIMESTAMP niedostępny, używam datetime.now(timezone.utc).")
        return datetime.now(timezone.utc)

def log_new_position(symbol: str, direction: str, entry: float, stoploss: float, target: float, position_id: str):
    if not firestore_db_client:
        print(f"[LOGGER_ERROR] Klient Firestore niedostępny. Nie można zalogować nowej pozycji {position_id} do Firestore.")
        # Fallback log do konsoli, jeśli Firestore jest niedostępny
        fallback_log_data = {
            "type": "NEW_PLANNED_FALLBACK", "position_id": position_id, "symbol": symbol,
            "direction": direction, "entry": entry, "sl": stoploss, "tp": target,
            "timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
        print(f"[FALLBACK_CONSOLE_LOG] {json.dumps(fallback_log_data)}")
        return

    position_doc = {
        # position_id będzie ID dokumentu Firestore
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry,
        "stop_loss": stoploss,
        "take_profit": target,
        "status": "planned",
        "planned_at": get_firestore_server_timestamp_or_fallback(),
        "opened_at": None,
        "closed_at": None,
        "cancelled_at": None,
        "result_reason": None,
        # Opcjonalnie: identyfikator instancji App Engine, która utworzyła/zaktualizowała pozycję
        "gae_instance_id": os.getenv("GAE_INSTANCE", "local_or_unknown")
    }
    try:
        firestore_db_client.collection(POSITIONS_COLLECTION_FIRESTORE).document(position_id).set(position_doc)
        print(f"[LOGGER_FIRESTORE] Zalogowano nową pozycję {position_id} do Firestore.")
    except Exception as e:
        print(f"[LOGGER_ERROR_FIRESTORE] Błąd zapisu nowej pozycji {position_id} do Firestore: {e}")
        fallback_log_data = {
            "type": "ERROR_LOGGING_NEW_FIRESTORE", "position_id": position_id, "data": position_doc,
            "error": str(e), "timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
        print(f"[FALLBACK_CONSOLE_LOG] {json.dumps(fallback_log_data, default=str)}") # default=str dla SERVER_TIMESTAMP

def update_position_status(position_id: str, status: PositionStatus, result_reason: Optional[str] = None):
    if not firestore_db_client:
        print(f"[LOGGER_ERROR] Klient Firestore niedostępny. Nie można zaktualizować statusu pozycji {position_id} w Firestore.")
        fallback_log_data = {
            "type": "UPDATE_STATUS_FALLBACK", "position_id": position_id, "new_status": status,
            "reason": result_reason, "timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
        print(f"[FALLBACK_CONSOLE_LOG] {json.dumps(fallback_log_data)}")
        return

    doc_ref = firestore_db_client.collection(POSITIONS_COLLECTION_FIRESTORE).document(position_id)
    update_data = {
        "status": status,
        "gae_instance_id": os.getenv("GAE_INSTANCE", "local_or_unknown") # Aktualizuj też ID instancji
    }
    timestamp_field = status + "_at" # np. "opened_at", "closed_at"
    update_data[timestamp_field] = get_firestore_server_timestamp_or_fallback()
    
    if result_reason:
        update_data["result_reason"] = result_reason
    
    try:
        # Sprawdź, czy dokument istnieje, zanim go zaktualizujesz, aby uniknąć niepotrzebnych błędów
        # lub po prostu wykonaj update, a Firestore zgłosi błąd, jeśli nie istnieje
        # Dla uproszczenia, wykonujemy update. Jeśli chcesz, możesz dodać doc_ref.get().exists()
        doc_ref.update(update_data) # Rzuci błąd, jeśli dokument nie istnieje
        print(f"[LOGGER_FIRESTORE] Zaktualizowano status pozycji {position_id} na {status} w Firestore.")
    except Exception as e: # Można bardziej szczegółowo łapać błędy Firestore, np. exceptions.NotFound
        print(f"[LOGGER_ERROR_FIRESTORE] Błąd aktualizacji statusu pozycji {position_id} na {status} w Firestore: {e}")
        # Jeśli dokument nie został znaleziony, może to być normalne w niektórych scenariuszach,
        # ale tutaj logujemy jako błąd.
        fallback_log_data = {
            "type": "ERROR_UPDATING_STATUS_FIRESTORE", "position_id": position_id, "update_data": update_data,
            "error": str(e), "timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
        print(f"[FALLBACK_CONSOLE_LOG] {json.dumps(fallback_log_data, default=str)}")