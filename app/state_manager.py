# app/state_manager.py

from collections import deque
from typing import Dict, Deque
from time import time

# Konfiguracja
MAX_OB_ALERTS = 3  # Przechowuj 3 ostatnie OrderBlocki
HEATMAP_TTL = 120  # sekundy

# Bufory per symbol
# Heatmapa = tuple (timestamp, alert)
data_store: Dict[str, Dict[str, Deque]] = {}


def init_symbol(symbol: str):
    if symbol not in data_store:
        data_store[symbol] = {
            "TOP_GREEN_CHANGE": deque(),
            "BOTTOM_RED_CHANGE": deque(),
            "OrderBlock": deque(maxlen=MAX_OB_ALERTS),
        }


def clean_expired_heatmap(symbol: str):
    now = time()
    for event in ("TOP_GREEN_CHANGE", "BOTTOM_RED_CHANGE"):
        filtered = deque([
            item for item in data_store[symbol][event]
            if now - item[0] <= HEATMAP_TTL
        ])
        data_store[symbol][event] = filtered


def update_alert(alert: dict):
    symbol = alert.get("symbol") or alert.get("ticker")
    if not symbol:
        return

    event_type = alert.get("event") or alert.get("type")
    if not event_type:
        return

    init_symbol(symbol)

    if event_type == "OrderBlock":
        data_store[symbol]["OrderBlock"].append(alert)
    elif event_type in ("TOP_GREEN_CHANGE", "BOTTOM_RED_CHANGE"):
        ob_list = data_store[symbol]["OrderBlock"]
        if not ob_list:
            return

        direction = ob_list[-1].get("direction")
        entry = float(ob_list[-1].get("entry", 0))
        stop = float(ob_list[-1].get("stoploss", 0))
        value = float(alert.get("value", 0))

        if direction == "long" and event_type == "TOP_GREEN_CHANGE" and stop < value < entry:
            data_store[symbol][event_type].append((time(), alert))
        elif direction == "short" and event_type == "BOTTOM_RED_CHANGE" and stop > value > entry:
            data_store[symbol][event_type].append((time(), alert))

    clean_expired_heatmap(symbol)


def get_last_heatmap(symbol: str, event_type: str) -> Deque[dict]:
    return deque([a[1] for a in data_store.get(symbol, {}).get(event_type, deque())])


def get_last_orderblocks(symbol: str) -> Deque[dict]:
    return data_store.get(symbol, {}).get("OrderBlock", deque())


def print_debug():
    for symbol, groups in data_store.items():
        print(f"\n=== {symbol} ===")
        for group, alerts in groups.items():
            print(f"{group}: {len(alerts)} alert(s)")
            for a in alerts:
                data = a[1] if isinstance(a, tuple) else a
                print(f"  -> {data.get('timestamp', '')} | {data.get('event', data.get('type'))}")


def process_alert(alert: dict):
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
