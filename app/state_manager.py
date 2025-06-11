# TRADING_BOT/app/state_manager.py

import logging
from typing import Dict, Deque, Optional, List, Any
from collections import deque

# Poprawne importy
from firebase_admin import firestore
from .firebase_client import get_db

logger = logging.getLogger(__name__)

# Definicje kolekcji Firestore dla spójności
PLANNED_POSITIONS_COLLECTION = "planned_positions"
OPENED_POSITIONS_COLLECTION = "opened_positions"

# Bufory alertów (w pamięci RAM)
alert_data_store: Dict[str, Dict[str, Deque[dict]]] = {}
MAX_HEATMAP_ALERTS = 5
MAX_OB_ALERTS = 2

# --- ZARZĄDZANIE ALERTAMI (w pamięci) ---

def init_symbol_alerts(symbol: str):
    """Inicjuje strukturę danych dla nowego symbolu w buforze alertów."""
    if symbol not in alert_data_store:
        alert_data_store[symbol] = {
            "TOP_GREEN_CHANGE": deque(maxlen=MAX_HEATMAP_ALERTS),
            "BOTTOM_RED_CHANGE": deque(maxlen=MAX_HEATMAP_ALERTS),
            "OrderBlock": deque(maxlen=MAX_OB_ALERTS),
        }

def process_alert(alert: dict):
    """Przetwarza przychodzący alert i dodaje go do odpowiedniego bufora w pamięci."""
    symbol = alert.get("symbol") or alert.get("ticker")
    event = alert.get("event") or alert.get("type")
    if not all([symbol, event]):
        logger.warning(f"Alert bez symbolu/eventu, pomijam: {alert.get('id')}")
        return
    init_symbol_alerts(symbol)
    if event in alert_data_store[symbol]:
        alert_data_store[symbol][event].append(alert)

def get_last_heatmap(symbol: str, event_type: str) -> Deque[dict]:
    """Zwraca bufor (deque) ostatnich alertów heatmapy dla danego symbolu."""
    return alert_data_store.get(symbol, {}).get(event_type, deque())

def get_last_orderblocks(symbol: str) -> Deque[dict]:
    """Zwraca bufor (deque) ostatnich alertów OrderBlock dla danego symbolu."""
    return alert_data_store.get(symbol, {}).get("OrderBlock", deque())

def get_all_alert_symbols() -> List[str]:
    """Zwraca listę wszystkich symboli, dla których mamy jakiekolwiek alerty w pamięci."""
    return list(alert_data_store.keys())

# --- ZARZĄDZANIE POZYCJAMI (w Firestore) ---

def generate_position_id(symbol: str, direction: str, entry_price: float) -> str:
    """Tworzy deterministyczne ID pozycji na podstawie kluczowych parametrów setupu."""
    return f"{symbol}_{direction}_{entry_price:.5f}"

def add_planned_position(details: Dict[str, Any]):
    """Zapisuje nową, zaplanowaną pozycję jako dokument w Firestore."""
    db = get_db()
    position_id = details["position_id"]
    try:
        # Sprawdzenie, czy pozycja o tym ID już istnieje
        doc_ref = db.collection(PLANNED_POSITIONS_COLLECTION).document(position_id)
        if doc_ref.get().exists:
            logger.warning(f"[STATE_MGR_WARN] Pozycja {position_id} już istnieje w planowanych. Pomijam dodanie.")
            return
        doc_ref.set(details)
        logger.info(f"[STATE_MGR_FIRESTORE] Dodano zaplanowaną pozycję do Firestore: {position_id}")
    except Exception as e:
        logger.error(f"[STATE_MGR_FIRESTORE_ERROR] Nie udało się zapisać planowanej pozycji {position_id}: {e}", exc_info=True)

def get_all_planned_for_symbol(symbol: str) -> List[Dict[str, Any]]:
    """Pobiera wszystkie zaplanowane pozycje dla danego symbolu z Firestore."""
    db = get_db()
    try:
        docs = db.collection(PLANNED_POSITIONS_COLLECTION).where("symbol", "==", symbol).stream()
        return [doc.to_dict() for doc in docs]
    except Exception as e:
        logger.error(f"[STATE_MGR_FIRESTORE_ERROR] Nie udało się pobrać planowanych pozycji dla {symbol}: {e}", exc_info=True)
        return []

def get_all_opened_for_symbol(symbol: str) -> List[Dict[str, Any]]:
    """Pobiera wszystkie otwarte pozycje dla danego symbolu z Firestore."""
    db = get_db()
    try:
        docs = db.collection(OPENED_POSITIONS_COLLECTION).where("symbol", "==", symbol).stream()
        return [doc.to_dict() for doc in docs]
    except Exception as e:
        logger.error(f"[STATE_MGR_FIRESTORE_ERROR] Nie udało się pobrać otwartych pozycji dla {symbol}: {e}", exc_info=True)
        return []

def get_all_position_symbols() -> List[str]:
    """Pobiera unikalną listę wszystkich symboli, które mają aktywne pozycje (planowane lub otwarte)."""
    db = get_db()
    symbols = set()
    try:
        planned_docs = db.collection(PLANNED_POSITIONS_COLLECTION).select(["symbol"]).stream()
        for doc in planned_docs:
            symbols.add(doc.to_dict().get("symbol"))
        
        opened_docs = db.collection(OPENED_POSITIONS_COLLECTION).select(["symbol"]).stream()
        for doc in opened_docs:
            symbols.add(doc.to_dict().get("symbol"))
    except Exception as e:
        logger.error(f"[STATE_MGR_FIRESTORE_ERROR] Nie udało się pobrać wszystkich symboli pozycji: {e}", exc_info=True)
    return list(symbols)

@firestore.transactional
def move_planned_to_opened_transactional(transaction, pos_id: str):
    """Atomowo przenosi pozycję z kolekcji 'planned' do 'opened'."""
    db = get_db()
    planned_ref = db.collection(PLANNED_POSITIONS_COLLECTION).document(pos_id)
    opened_ref = db.collection(OPENED_POSITIONS_COLLECTION).document(pos_id)
    
    planned_snapshot = planned_ref.get(transaction=transaction)
    if not planned_snapshot.exists:
        logger.warning(f"[TRANSACTION_WARN] Pozycja {pos_id} nie istnieje już w planowanych. Prawdopodobnie anulowana lub otwarta przez inny cykl.")
        return False
        
    position_details = planned_snapshot.to_dict()
    
    transaction.set(opened_ref, position_details)
    transaction.delete(planned_ref)
    logger.info(f"[TRANSACTION_SUCCESS] Pozycja {pos_id} przeniesiona atomowo do otwartych.")
    return True

@firestore.transactional
def remove_position_transactional(transaction, collection: str, pos_id: str):
    """Atomowo usuwa pozycję z danej kolekcji ('planned' lub 'opened')."""
    db = get_db()
    doc_ref = db.collection(collection).document(pos_id)
    
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        logger.warning(f"[TRANSACTION_WARN] Pozycja {pos_id} nie istnieje już w kolekcji '{collection}'.")
        return False

    transaction.delete(doc_ref)
    logger.info(f"[TRANSACTION_SUCCESS] Pozycja {pos_id} atomowo usunięta z kolekcji '{collection}'.")
    return True