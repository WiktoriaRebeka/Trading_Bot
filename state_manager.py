# /trading_bot/state_manager.py (WERSJA FINALNA Z FIRESTORE)

import logging
from typing import Dict, Optional, Any, List
from firebase_client import get_db
from google.cloud.firestore_v1.base_client import BaseClient

logger = logging.getLogger(__name__)

# Nazwa kolekcji w Firestore, która będzie przechowywać stan.
STATE_COLLECTION = "trading_state"

def _get_db() -> BaseClient:
    """Helper do uzyskania instancji DB."""
    return get_db()

def set_active_setup(symbol: str, ob_data: dict):
    """Zapisuje lub nadpisuje setup dla symbolu bezpośrednio w Firestore."""
    db = _get_db()
    doc_ref = db.collection(STATE_COLLECTION).document(symbol)
    state_data = {
        'ob_data': ob_data,
        'is_new': True,
        'is_position_open': False,
        'last_known_price': None,
        'position_data': None
    }
    doc_ref.set(state_data)
    logger.info(f"[{symbol}] Zapisano/zaktualizowano setup w Firestore.")

def get_active_setup(symbol: str) -> Optional[Dict[str, Any]]:
    """Odczytuje aktywny setup dla symbolu z Firestore."""
    db = _get_db()
    doc_ref = db.collection(STATE_COLLECTION).document(symbol)
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    return None

def get_all_active_symbols() -> List[str]:
    """Pobiera listę wszystkich dokumentów (czyli aktywnych symboli) z kolekcji stanu."""
    db = _get_db()
    docs = db.collection(STATE_COLLECTION).stream()
    return [doc.id for doc in docs]

def remove_setup(symbol: str):
    """Całkowicie usuwa setup dla danego symbolu z Firestore."""
    db = _get_db()
    doc_ref = db.collection(STATE_COLLECTION).document(symbol)
    doc_ref.delete()
    logger.info(f"[{symbol}] Zakończony setup został usunięty ze stanu w Firestore.")

def set_position_status(symbol: str, is_open: bool, trade_details: Optional[Dict] = None):
    """Ustawia flagę pozycji i przechowuje szczegóły transakcji w Firestore."""
    db = _get_db()
    doc_ref = db.collection(STATE_COLLECTION).document(symbol)
    
    if is_open and trade_details:
        update_data = {
            'is_position_open': True,
            'position_data': trade_details
        }
        doc_ref.update(update_data)
        logger.info(f"[{symbol}] Zaktualizowano status pozycji na OTWARTA w Firestore. ID: {trade_details.get('trade_id')}")
    else:
        # Ta funkcja nie powinna być już używana do zamykania - do tego służy remove_setup
        logger.warning(f"[{symbol}] Wywołano set_position_status z is_open=False. Należy użyć remove_setup().")
        remove_setup(symbol)

def update_last_known_price(symbol: str, price: Optional[float]):
    """Aktualizuje ostatnią znaną cenę w dokumencie stanu w Firestore."""
    if price is None:
        return
    db = _get_db()
    doc_ref = db.collection(STATE_COLLECTION).document(symbol)
    doc_ref.update({'last_known_price': price})

def update_max_profit_price(symbol: str, new_max_profit_price: float):
    """Aktualizuje cenę maksymalnego osiągniętego profitu w Firestore."""
    db = _get_db()
    doc_ref = db.collection(STATE_COLLECTION).document(symbol)
    doc_ref.update({'position_data.max_profit_price': new_max_profit_price})

def update_rr_flags(symbol: str, updated_flags: Dict[str, bool]):
    """Aktualizuje flagi RR w dokumencie stanu w Firestore."""
    db = _get_db()
    doc_ref = db.collection(STATE_COLLECTION).document(symbol)
    # Używamy notacji kropkowej do aktualizacji zagnieżdżonych pól
    update_payload = {f'position_data.rr_achieved_flags.{key}': value for key, value in updated_flags.items()}
    doc_ref.update(update_payload)