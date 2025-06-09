# TRADING_BOT/app/positions_logger.py
import json
import os
from datetime import datetime, timezone
from typing import Optional, Literal
import sys # Dla logowania do stderr

# Importujemy tylko sam moduł firestore, aby mieć dostęp do firestore.SERVER_TIMESTAMP
# Klient db (firestore_db_client) zostanie ustawiony przez funkcję z app/main.py
try:
    from firebase_admin import firestore as firebase_firestore_module_for_logger
    sys.stderr.write("[POS_LOGGER] Moduł firebase_admin.firestore (jako firebase_firestore_module_for_logger) zaimportowany pomyślnie.\n")
    sys.stderr.flush()
except ImportError:
    sys.stderr.write("[POS_LOGGER_CRITICAL] Nie można zaimportować firebase_admin.firestore. Logowanie pozycji do Firestore nie będzie działać poprawnie.\n")
    sys.stderr.flush()
    firebase_firestore_module_for_logger = None

firestore_db_client = None # Zostanie ustawiony przez set_firestore_client_for_logger

POSITIONS_COLLECTION_FIRESTORE = "trading_positions"
PositionStatus = Literal["planned", "opened", "closed", "cancelled"]

def set_firestore_client_for_logger(client):
    """Funkcja pomocnicza do ustawienia klienta Firestore z app/main.py"""
    global firestore_db_client
    firestore_db_client = client
    sys.stderr.write(f"[POS_LOGGER] Klient Firestore ustawiony dla loggera: {firestore_db_client}\n")
    sys.stderr.flush()

def get_firestore_server_timestamp_or_fallback():
    """Zwraca firestore.SERVER_TIMESTAMP jeśli dostępne, w przeciwnym razie czas klienta UTC."""
    if firestore_db_client and firebase_firestore_module_for_logger:
        return firebase_firestore_module_for_logger.SERVER_TIMESTAMP
    else:
        # Używamy print, aby było zgodne z resztą logów stdout/stderr
        print("[POS_LOGGER_WARN] firestore.SERVER_TIMESTAMP niedostępny, używam datetime.now(timezone.utc).")
        return datetime.now(timezone.utc)

def log_new_position(symbol: str, direction: str, entry: float, stoploss: float, target: float, position_id: str):
    if not firestore_db_client:
        print(f"[LOGGER_ERROR] Klient Firestore niedostępny. Nie można zalogować nowej pozycji {position_id} do Firestore.")
        fallback_log_data = {
            "type": "NEW_PLANNED_FALLBACK", "position_id": position_id, "symbol": symbol,
            "direction": direction, "entry": entry, "sl": stoploss, "tp": target,
            "timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
        print(f"[FALLBACK_CONSOLE_LOG] {json.dumps(fallback_log_data)}")
        return

    position_doc = {
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
        print(f"[FALLBACK_CONSOLE_LOG] {json.dumps(fallback_log_data, default=str)}")

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
        "gae_instance_id": os.getenv("GAE_INSTANCE", "local_or_unknown")
    }
    timestamp_field = status + "_at"
    update_data[timestamp_field] = get_firestore_server_timestamp_or_fallback()
    
    if result_reason:
        update_data["result_reason"] = result_reason
    
    try:
        doc_ref.update(update_data)
        print(f"[LOGGER_FIRESTORE] Zaktualizowano status pozycji {position_id} na {status} w Firestore.")
    except Exception as e:
        print(f"[LOGGER_ERROR_FIRESTORE] Błąd aktualizacji statusu pozycji {position_id} na {status} w Firestore: {e}")
        fallback_log_data = {
            "type": "ERROR_UPDATING_STATUS_FIRESTORE", "position_id": position_id, "update_data": update_data,
            "error": str(e), "timestamp_utc": datetime.now(timezone.utc).isoformat()
        }
        print(f"[FALLBACK_CONSOLE_LOG] {json.dumps(fallback_log_data, default=str)}")

sys.stderr.write("[POS_LOGGER] Moduł positions_logger.py załadowany.\n")
sys.stderr.flush()