# TRADING_BOT/app/state_manager.py

import logging
from typing import Dict, Deque, Optional, List, Any
from collections import deque

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
        logger.debug(f"[STATE_MGR] Inicjuję bufor alertów dla nowego symbolu: {symbol}")
        alert_data_store[symbol] = {
            "TOP_GREEN_CHANGE": deque(maxlen=MAX_HEATMAP_ALERTS),
            "BOTTOM_RED_CHANGE": deque(maxlen=MAX_HEATMAP_ALERTS),
            "OrderBlock": deque(maxlen=MAX_OB_ALERTS),
        }

def process_alert(alert: dict):
    """Przetwarza przychodzący alert i dodaje go do odpowiedniego bufora w pamięci."""
    symbol = alert.get("symbol") or alert.get("ticker")
    # Zmieniamy 'event' na 'type', aby było spójne z nowymi alertami
    event = alert.get("type")
    
    if not all([symbol, event]):
        logger.warning(f"Alert bez symbolu lub typu, pomijam: {alert}")
        return
        
    init_symbol_alerts(symbol)
    
    if event in alert_data_store[symbol]:
        logger.debug(f"[STATE_MGR] Dodaję alert do bufora [{symbol}][{event}]")
        alert_data_store[symbol][event].append(alert)
    else:
        logger.warning(f"[STATE_MGR] Otrzymano nieznany typ eventu '{event}' dla symbolu {symbol}. Pomijam.")

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

# ==============================================================================
# === KLUCZOWA POPRAWKA ===
# Zmieniamy definicję funkcji, aby przyjmowała 4 argumenty, w tym timestamp.
# ==============================================================================
def generate_position_id(symbol: str, direction: str, entry_price: float, ob_timestamp: str) -> str:
    """Tworzy unikalne ID pozycji na podstawie parametrów i timestampu OrderBlocka."""
    # Usuwamy znaki specjalne z timestampu, aby był bezpieczny jako ID dokumentu
    safe_timestamp = ob_timestamp.replace(":", "-").replace(".", "_").replace("+", "plus").replace("Z", "")
    return f"{symbol}_{direction}_{entry_price:.5f}_{safe_timestamp}"

def add_planned_position(details: Dict[str, Any]):
    """Zapisuje nową, zaplanowaną pozycję jako dokument w Firestore."""
    db = get_db()
    position_id = details.get("position_id")
    if not position_id:
        logger.error(f"[STATE_MGR_ERROR] Próba dodania planowanej pozycji bez ID. Szczegóły: {details}")
        return
        
    try:
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
            if doc and doc.exists and doc.to_dict().get("symbol"):
                symbols.add(doc.to_dict().get("symbol"))
        
        opened_docs = db.collection(OPENED_POSITIONS_COLLECTION).select(["symbol"]).stream()
        for doc in opened_docs:
            if doc and doc.exists and doc.to_dict().get("symbol"):
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
        logger.warning(f"[TRANSACTION_WARN] Pozycja {pos_id} nie istnieje już w planowanych.")
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

# Opcjonalna funkcja do debugowania stanu w pamięci
def print_state_summary():
    logger.debug("--- PODSUMOWANIE STANU PAMIĘCI ---")
    if not alert_data_store:
        logger.debug("Brak danych alertów w pamięci.")
        return
    for symbol, events in alert_data_store.items():
        logger.debug(f"Symbol: {symbol}")
        for event_type, alerts_deque in events.items():
            if alerts_deque:
                logger.debug(f"  -> {event_type} (ilość: {len(alerts_deque)}): Najnowszy = {alerts_deque[-1]}")
    logger.debug("---------------------------------")


    # W app/state_manager.py

# ... (na górze pliku, obok innych zmiennych globalnych)
# Nowa struktura do śledzenia stanu strategii dla każdego symbolu
strategy_state_store: Dict[str, Dict[str, Any]] = {}

# ... (reszta pliku)

# === NOWE FUNKCJE NA KOŃCU PLIKU ===

def set_active_order_block(symbol: str, ob_data: dict):
    """Ustawia nowy aktywny OrderBlock i resetuje jego stan."""
    if symbol not in strategy_state_store:
        strategy_state_store[symbol] = {}
    strategy_state_store[symbol]['active_order_block'] = ob_data
    strategy_state_store[symbol]['is_ob_mitigated'] = False
    logger.info(f"[{symbol}] Ustawiono nowy aktywny OrderBlock: {ob_data}")

def get_active_order_block(symbol: str) -> Optional[dict]:
    """Pobiera aktualnie śledzony OrderBlock."""
    return strategy_state_store.get(symbol, {}).get('active_order_block')

def is_ob_mitigated(symbol: str) -> bool:
    """Sprawdza, czy aktywny OB został już zmitigowany."""
    return strategy_state_store.get(symbol, {}).get('is_ob_mitigated', True) # Domyślnie True, jeśli nie ma stanu

def set_ob_as_mitigated(symbol: str):
    """Oznacza aktywny OrderBlock jako zmitigowany/zużyty."""
    if symbol in strategy_state_store:
        strategy_state_store[symbol]['is_ob_mitigated'] = True
        logger.info(f"[{symbol}] Aktywny OrderBlock został oznaczony jako zmitigowany.")