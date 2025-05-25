# app/state_manager.py

from collections import deque
from typing import Dict, Deque

# Konfiguracja ile alertów pamiętać
MAX_HEATMAP_ALERTS = 5
MAX_OB_ALERTS = 2

# Bufory per symbol
data_store: Dict[str, Dict[str, Deque[dict]]] = {}

def init_symbol(symbol: str):
    if symbol not in data_store:
        data_store[symbol] = {
            "TOP_GREEN_CHANGE": deque(maxlen=MAX_HEATMAP_ALERTS),
            "BOTTOM_RED_CHANGE": deque(maxlen=MAX_HEATMAP_ALERTS),
            "OrderBlock": deque(maxlen=MAX_OB_ALERTS),
        }

def update_alert(alert: dict):
    symbol = alert.get("symbol") or alert.get("ticker")
    if not symbol:
        return

    event_type = alert.get("event") or alert.get("type")
    if not event_type:
        return

    init_symbol(symbol)

    if event_type in ("TOP_GREEN_CHANGE", "BOTTOM_RED_CHANGE"):
        data_store[symbol][event_type].append(alert)
    elif event_type == "OrderBlock":
        data_store[symbol]["OrderBlock"].append(alert)

def get_last_heatmap(symbol: str, event_type: str) -> Deque[dict]:
    return data_store.get(symbol, {}).get(event_type, deque())

def get_last_orderblocks(symbol: str) -> Deque[dict]:
    return data_store.get(symbol, {}).get("OrderBlock", deque())

def print_debug():
    for symbol, groups in data_store.items():
        print(f"\n=== {symbol} ===")
        for group, alerts in groups.items():
            print(f"{group}: {len(alerts)} alert(s)")
            for a in alerts:
                print(f"  -> {a.get('timestamp', '')} | {a.get('event', a.get('type'))}")

def process_alert(alert: dict):
    """
    Procesuje nowy alert JSON.
    - Sprawdza, czy zawiera niezbędne pola
    - Aktualizuje bufory w data_store
    """
    if not isinstance(alert, dict):
        print("[❌] Alert nie jest dict:", alert)
        return

    symbol = alert.get("symbol") or alert.get("ticker")
    event = alert.get("event") or alert.get("type")
    if not symbol or not event:
        print("[⚠️] Alert bez symbolu lub eventu:", alert)
        return

    update_alert(alert)
    print(f"[✅] Zarejestrowano alert: {symbol} | {event}")
