# TRADING_BOT/app/positions_logger.py
import json
import os
from datetime import datetime, timezone
from typing import Optional, Literal
import logging # Zmieniamy na logging

# Importuj funkcję get_db z firebase_client
from .firebase_client import get_db
# Importujemy tylko sam moduł firestore, aby mieć dostęp do firestore.SERVER_TIMESTAMP
from firebase_admin import firestore as firebase_firestore_module 

logger = logging.getLogger(__name__) # Używamy loggera

POSITIONS_COLLECTION_FIRESTORE = "trading_positions" # Można przenieść do constants.py
PositionStatus = Literal["planned", "opened", "closed", "cancelled"]

# Funkcja set_firestore_client_for_logger NIE JEST JUŻ POTRZEBNA

def get_firestore_server_timestamp_or_fallback():
    """Zwraca firestore.SERVER_TIMESTAMP jeśli dostępne, w przeciwnym razie czas klienta UTC."""
    # Sprawdzenie firebase_firestore_module jest wystarczające, bo get_db() rzuci wyjątkiem, jeśli Firebase nie działa
    if firebase_firestore_module:
        return firebase_firestore_module.SERVER_TIMESTAMP
    else:
        logger.warning("[POS_LOGGER_WARN] firebase_firestore_module (dla SERVER_TIMESTAMP) niedostępny, używam datetime.now(timezone.utc).")
        return datetime.now(timezone.utc)

def log_new_position(symbol: str, direction: str, entry: float, stoploss: float, target: float, position_id: str, triggering_ob_timestamp: str):
    try:
        db = get_db() # Pobierz klienta Firestore na początku funkcji
    except Exception as e_db:
        logger.error(f"[LOGGER_ERROR] Nie udało się uzyskać klienta Firestore w log_new_position: {e_db}", exc_info=True)
        # Możesz tu dodać logikę fallback, jeśli chcesz
        return

    position_doc = {
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry,
        "stop_loss": stoploss,
        "take_profit": target,
        "status": "planned",
        "planned_at": get_firestore_server_timestamp_or_fallback(),
        "triggering_ob_timestamp": triggering_ob_timestamp,
        "opened_at": None,
        "closed_at": None,
        "cancelled_at": None,
        "result_reason": None,
        "actual_close_price": None, # Dodajemy to pole, będzie aktualizowane przy zamknięciu
        "gae_instance_id": os.getenv("GAE_INSTANCE", "local_or_unknown")
    }
    try:
        db.collection(POSITIONS_COLLECTION_FIRESTORE).document(position_id).set(position_doc)
        logger.info(f"[LOGGER_FIRESTORE] Zalogowano nową pozycję {position_id} do Firestore.")
    except Exception as e:
        logger.error(f"[LOGGER_ERROR_FIRESTORE] Błąd zapisu nowej pozycji {position_id} do Firestore: {e}", exc_info=True)
        # Logika fallback (opcjonalna)
        # fallback_log_data = { ... }
        # print(f"[FALLBACK_CONSOLE_LOG] {json.dumps(fallback_log_data, default=str)}")

def update_position_status(position_id: str, status: PositionStatus, result_reason: Optional[str] = None, actual_close_price: Optional[float] = None): # Dodany argument actual_close_price
    try:
        db = get_db() # Pobierz klienta Firestore na początku funkcji
    except Exception as e_db:
        logger.error(f"[LOGGER_ERROR] Nie udało się uzyskać klienta Firestore w update_position_status: {e_db}", exc_info=True)
        return

    doc_ref = db.collection(POSITIONS_COLLECTION_FIRESTORE).document(position_id)
    update_data = {
        "status": status,
        "gae_instance_id": os.getenv("GAE_INSTANCE", "local_or_unknown")
    }
    timestamp_field = status + "_at" # np. "opened_at", "closed_at", "cancelled_at"
    update_data[timestamp_field] = get_firestore_server_timestamp_or_fallback()
    
    if result_reason:
        update_data["result_reason"] = result_reason
    if actual_close_price is not None: # Zapisz, jeśli podano
        update_data["actual_close_price"] = actual_close_price
    
    try:
        doc_ref.update(update_data) # Użyj update zamiast set, aby nie nadpisać całego dokumentu, jeśli tylko aktualizujesz status
        logger.info(f"[LOGGER_FIRESTORE] Zaktualizowano status pozycji {position_id} na '{status}' w Firestore.")
    except Exception as e:
        logger.error(f"[LOGGER_ERROR_FIRESTORE] Błąd aktualizacji statusu pozycji {position_id} na '{status}' w Firestore: {e}", exc_info=True)
        # Logika fallback (opcjonalna)

logger.info("[POS_LOGGER] Moduł positions_logger.py załadowany (używa get_db).")