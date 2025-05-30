# trading_bot/app/state_manager.py
from collections import deque
from typing import Dict, Deque, Optional, List, Any
from app.constants import MAX_ALERT_AGE_SECONDS # Jeśli chcesz go tu używać, inaczej z bot_logic
from datetime import datetime, timezone

# Konfiguracja ile alertów pamiętać
MAX_HEATMAP_ALERTS = 5
MAX_OB_ALERTS = 2 # Ostatni i przedostatni, aby wykryć zmianę

# Bufory alertów per symbol
alert_data_store: Dict[str, Dict[str, Deque[dict]]] = {}

# Bufory pozycji
# Klucz: unikalny identyfikator pozycji (np. f"{symbol}_{direction}_{entry_price_str}_{timestamp_planowania}")
# Wartość: słownik z detalami pozycji
planned_positions: Dict[str, Dict[str, Any]] = {}
opened_positions: Dict[str, Dict[str, Any]] = {}


def init_symbol_alerts(symbol: str):
    if symbol not in alert_data_store:
        alert_data_store[symbol] = {
            "TOP_GREEN_CHANGE": deque(maxlen=MAX_HEATMAP_ALERTS),
            "BOTTOM_RED_CHANGE": deque(maxlen=MAX_HEATMAP_ALERTS),
            "OrderBlock": deque(maxlen=MAX_OB_ALERTS),
        }

def _update_alert_in_store(alert: dict):
    symbol = alert.get("symbol") or alert.get("ticker")
    event_type = alert.get("event") or alert.get("type")

    init_symbol_alerts(symbol) # Upewnij się, że symbol istnieje

    if event_type in ("TOP_GREEN_CHANGE", "BOTTOM_RED_CHANGE"):
        alert_data_store[symbol][event_type].append(alert)
    elif event_type == "OrderBlock":
        alert_data_store[symbol]["OrderBlock"].append(alert)

def process_alert(alert: dict):
    """
    Procesuje nowy alert JSON: waliduje i aktualizuje bufory w alert_data_store.
    """
    if not isinstance(alert, dict):
        print(f"[STATE_MGR_ERROR] Alert nie jest dict: {alert}")
        return

    symbol = alert.get("symbol") or alert.get("ticker")
    event = alert.get("event") or alert.get("type")
    timestamp = alert.get("timestamp") or alert.get("received_at") # Potrzebny do is_recent

    if not symbol or not event or not timestamp:
        print(f"[STATE_MGR_WARN] Alert bez symbolu, eventu lub timestampu, pomijanie: {alert.get('id')}")
        return

    _update_alert_in_store(alert)
    # print(f"[STATE_MGR] Zarejestrowano alert: {symbol} | {event}") # Można odkomentować dla częstego logowania

def get_last_heatmap(symbol: str, event_type: str) -> Deque[dict]:
    return alert_data_store.get(symbol, {}).get(event_type, deque())

def get_last_orderblocks(symbol: str) -> Deque[dict]:
    return alert_data_store.get(symbol, {}).get("OrderBlock", deque())

def get_all_alert_symbols() -> List[str]:
    return list(alert_data_store.keys())

# --- Zarządzanie Pozycjami ---

def generate_position_id(symbol: str, direction: str, entry_price: float, planned_at_iso: str) -> str:
    # Użyj entry_price sformatowanego do kilku miejsc po przecinku, aby uniknąć problemów z float
    return f"{symbol}_{direction}_{entry_price:.5f}_{planned_at_iso}"

def add_planned_position(position_id: str, details: Dict[str, Any]):
    if position_id in planned_positions or position_id in opened_positions:
        print(f"[STATE_MGR_WARN] Pozycja o ID {position_id} już istnieje. Nie dodaję duplikatu.")
        return
    planned_positions[position_id] = details
    print(f"[STATE_MGR] Dodano zaplanowaną pozycję: {position_id}")

def get_planned_position(position_id: str) -> Optional[Dict[str, Any]]:
    return planned_positions.get(position_id)

def get_all_planned_positions_for_symbol(symbol: str) -> List[Dict[str, Any]]:
    return [details for pos_id, details in planned_positions.items() if details["symbol"] == symbol]

def remove_planned_position(position_id: str) -> Optional[Dict[str, Any]]:
    if position_id in planned_positions:
        print(f"[STATE_MGR] Usunięto/Anulowano zaplanowaną pozycję: {position_id}")
        return planned_positions.pop(position_id)
    return None

def move_planned_to_opened(position_id: str):
    position_details = planned_positions.pop(position_id, None)
    if position_details:
        if position_id in opened_positions:
            print(f"[STATE_MGR_WARN] Pozycja {position_id} już istnieje w otwartych. Nie przenoszę.")
            # Przywróć do planowanych, jeśli to błąd logiki gdzie indziej
            # planned_positions[position_id] = position_details 
            return
        opened_positions[position_id] = position_details
        print(f"[STATE_MGR] Przeniesiono pozycję do otwartych: {position_id}")
    else:
        print(f"[STATE_MGR_WARN] Nie znaleziono pozycji {position_id} w planowanych do przeniesienia.")

def get_opened_position(position_id: str) -> Optional[Dict[str, Any]]:
    return opened_positions.get(position_id)

def get_all_opened_positions_for_symbol(symbol: str) -> List[Dict[str, Any]]:
    return [details for pos_id, details in opened_positions.items() if details["symbol"] == symbol]

def remove_opened_position(position_id: str) -> Optional[Dict[str, Any]]:
    if position_id in opened_positions:
        print(f"[STATE_MGR] Usunięto/Zamknięto otwartą pozycję: {position_id}")
        return opened_positions.pop(position_id)
    return None
    
def get_all_position_symbols() -> List[str]:
    symbols = set()
    symbols.update(details["symbol"] for details in planned_positions.values())
    symbols.update(details["symbol"] for details in opened_positions.values())
    return list(symbols)

def print_state_summary():
    print("\n--- Podsumowanie Stanu ---")
    print(f"Alerty: {len(alert_data_store)} symbole")
    # for symbol, groups in alert_data_store.items():
    #     print(f"  {symbol}: OB={len(groups.get('OrderBlock',[]))}, TG={len(groups.get('TOP_GREEN_CHANGE',[]))}, BR={len(groups.get('BOTTOM_RED_CHANGE',[]))}")
    print(f"Planowane pozycje: {len(planned_positions)}")
    for pos_id, details in planned_positions.items():
        print(f"  -> Planned: {pos_id} (Entry: {details['entry_price']})")
    print(f"Otwarte pozycje: {len(opened_positions)}")
    for pos_id, details in opened_positions.items():
        print(f"  -> Opened: {pos_id} (Entry: {details['entry_price']}, SL: {details['stop_loss']}, TP: {details['take_profit']})")
    print("-------------------------\n")