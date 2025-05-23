# app/bot_logic.py

import requests
from typing import Optional
from app import state_manager

BYBIT_TICKER_URL = "https://api.bybit.com/v2/public/tickers"

# Pobiera aktualna cene waloru (last_price) z API Bybit
def get_current_price(symbol: str) -> Optional[float]:
    try:
        response = requests.get(BYBIT_TICKER_URL)
        data = response.json()
        for item in data['result']:
            if item['symbol'] == symbol:
                return float(item['last_price'])
    except Exception as e:
        print(f"[BŁĄD] get_current_price({symbol}): {e}")
    return None

# Główna funkcja analizy alertów i ustawiania pozycji (LONG)
def check_long_entry(symbol: str):
    price = get_current_price(symbol)
    if price is None:
        return

    ob_alerts = state_manager.get_last_orderblocks(symbol)
    if not ob_alerts:
        return

    last_ob = ob_alerts[-1]
    if last_ob.get("direction") != "long":
        return

    entry = float(last_ob.get("entry"))
    stoploss = float(last_ob.get("stoploss"))
    target = float(last_ob.get("TP", 0))

    heatmap_alerts = state_manager.get_last_heatmap(symbol, "TOP_GREEN_CHANGE")
    if not heatmap_alerts:
        return

    last_heatmap = heatmap_alerts[-1]
    top_green = float(last_heatmap.get("value"))

    if stoploss < top_green < entry:
        print(f"[✅] WARUNEK LONG TRUE: {symbol} | Cena: {price}")
        print(f"[INFO] ENTRY: {entry} | SL: {stoploss} | TP: {target}")
    else:
        print(f"[⛔] WARUNEK LONG FALSE: {symbol} | Cena: {price} | TOP GREEN: {top_green}")

# Główna funkcja analizy alertów i ustawiania pozycji (SHORT)
def check_short_entry(symbol: str):
    price = get_current_price(symbol)
    if price is None:
        return

    ob_alerts = state_manager.get_last_orderblocks(symbol)
    if not ob_alerts:
        return

    last_ob = ob_alerts[-1]
    if last_ob.get("direction") != "short":
        return

    entry = float(last_ob.get("entry"))
    stoploss = float(last_ob.get("stoploss"))
    target = float(last_ob.get("TP", 0))

    heatmap_alerts = state_manager.get_last_heatmap(symbol, "BOTTOM_RED_CHANGE")
    if not heatmap_alerts:
        return

    last_heatmap = heatmap_alerts[-1]
    bottom_red = float(last_heatmap.get("value"))

    if stoploss > bottom_red > entry:
        print(f"[✅] WARUNEK SHORT TRUE: {symbol} | Cena: {price}")
        print(f"[INFO] ENTRY: {entry} | SL: {stoploss} | TP: {target}")
    else:
        print(f"[⛔] WARUNEK SHORT FALSE: {symbol} | Cena: {price} | BOTTOM RED: {bottom_red}")
