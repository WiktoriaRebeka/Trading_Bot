# TRADING_BOT/app/bot_logic.py

import logging
import requests
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta, timezone

# Poprawne importy
from firebase_admin import firestore
from .firebase_client import get_db
from . import state_manager
from .positions_logger import log_new_position, update_position_status
from .constants import (
    BYBIT_API_URL_V5_TICKERS,
    BYBIT_DEFAULT_CATEGORY,
    MAX_ALERT_AGE_SECONDS
)

logger = logging.getLogger(__name__)

# --- FUNKCJE POMOCNICZE ---

def get_all_prices_for_category(category: str = BYBIT_DEFAULT_CATEGORY) -> Dict[str, float]:
    """Pobiera ceny dla wszystkich symboli w danej kategorii za jednym zapytaniem."""
    params = {"category": category}
    headers = {'User-Agent': 'TradingBot/1.0', 'Accept': 'application/json'}
    all_prices = {}
    try:
        response = requests.get(BYBIT_API_URL_V5_TICKERS, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
            for ticker in data["result"]["list"]:
                symbol = ticker.get("symbol")
                price_str = ticker.get("lastPrice")
                if symbol and price_str:
                    try:
                        all_prices[symbol] = float(price_str)
                    except ValueError:
                        continue
            logger.info(f"Pobrano ceny dla {len(all_prices)} symboli.")
            return all_prices
    except Exception as e:
        logger.error(f"Nie udało się pobrać wszystkich cen: {e}", exc_info=True)
    return {}

# --- GŁÓWNA LOGIKA BOTA ---

def check_for_new_setups(symbol: str):
    """Sprawdza, czy najnowsze alerty tworzą prawidłowy, nowy setup do zaplanowania."""
    last_ob_alert_q = state_manager.get_last_orderblocks(symbol)
    if not last_ob_alert_q: return
    last_ob = last_ob_alert_q[-1]
    
    direction = last_ob.get("direction", "").lower()
    if not direction: return

    heatmap_event_type = "TOP_GREEN_CHANGE" if direction == "long" else "BOTTOM_RED_CHANGE"
    last_heatmap_alert_q = state_manager.get_last_heatmap(symbol, heatmap_event_type)
    if not last_heatmap_alert_q: return
    last_heatmap = last_heatmap_alert_q[-1]

    try:
        ob_ts = datetime.fromisoformat(last_ob["timestamp"].replace("Z", "+00:00"))
        heatmap_ts = datetime.fromisoformat(last_heatmap["timestamp"].replace("Z", "+00:00"))

        if abs(ob_ts - heatmap_ts) > timedelta(seconds=MAX_ALERT_AGE_SECONDS):
            logger.debug(f"Pominięto setup dla {symbol}: alerty OB i Heatmap są od siebie zbyt daleko w czasie.")
            return

        ob_entry = float(last_ob["entry"])
        ob_sl = float(last_ob["sl"])
        heatmap_value = float(last_heatmap["value"])
        
        position_id = state_manager.generate_position_id(symbol, direction, ob_entry)

        condition_met = (direction == "long" and ob_sl < heatmap_value < ob_entry) or \
                        (direction == "short" and ob_entry < heatmap_value < ob_sl)
        
        if condition_met:
            position_details = {
                "position_id": position_id, "symbol": symbol, "direction": direction,
                "entry_price": ob_entry, "stop_loss": ob_sl, "take_profit": float(last_ob["tp"]),
                "status": "planned", "planned_at": firestore.SERVER_TIMESTAMP,
                "triggering_ob_timestamp": last_ob["timestamp"],
                "triggering_ob_level_low": float(last_ob.get("levelLow", 0)),
                "triggering_ob_level_high": float(last_ob.get("levelHigh", 0)),
                "triggering_heatmap_value_at_planning": heatmap_value
            }
            state_manager.add_planned_position(position_details)
            log_new_position(symbol, direction, ob_entry, ob_sl, float(last_ob["tp"]), position_id)
            logger.info(f"[✅ PLAN] {symbol} | Entry: {ob_entry} | SL: {ob_sl} | Heatmap: {heatmap_value}")

    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[PLAN_ERROR] Błąd przetwarzania danych dla {symbol}: {e}", exc_info=True)

def monitor_positions(all_prices: Dict[str, float]):
    """Główna funkcja monitorująca, wywoływana w każdym cyklu bota."""
    all_symbols_with_positions = state_manager.get_all_position_symbols()
    
    for symbol in all_symbols_with_positions:
        # Konwersja ETHUSDT.P -> ETHUSDT
        current_price = all_prices.get(symbol.replace(".P", ""))
        if current_price is None:
            logger.warning(f"Brak aktualnej ceny dla {symbol}, pomijam monitorowanie.")
            continue
            
        # Monitorowanie planowanych pozycji (anulowanie lub otwarcie)
        planned_positions = state_manager.get_all_planned_for_symbol(symbol)
        for pos in planned_positions:
            check_and_process_planned_position(pos, current_price)
            
        # Monitorowanie otwartych pozycji (SL/TP)
        opened_positions = state_manager.get_all_opened_for_symbol(symbol)
        for pos in opened_positions:
            check_and_process_opened_position(pos, current_price)

def check_and_process_planned_position(planned_pos: Dict[str, Any], current_price: float):
    """Sprawdza jedną zaplanowaną pozycję pod kątem anulowania lub otwarcia."""
    pos_id = planned_pos["position_id"]
    symbol = planned_pos["symbol"]
    direction = planned_pos["direction"]
    db = get_db()

    cancel_reason = check_cancellation_conditions(planned_pos)
    if cancel_reason:
        logger.info(f"[ANULOWANIE] {pos_id} dla {symbol}: {cancel_reason}")
        transaction = db.transaction()
        if state_manager.remove_position_transactional(transaction, state_manager.PLANNED_POSITIONS_COLLECTION, pos_id):
            update_position_status(pos_id, "cancelled", result_reason=cancel_reason)
        return

    entry_price = planned_pos["entry_price"]
    should_open = (direction == "long" and current_price >= entry_price) or \
                  (direction == "short" and current_price <= entry_price)

    if should_open:
        logger.info(f"[OTWARCIE] Aktywacja pozycji {pos_id} dla {symbol} przy cenie {current_price}")
        transaction = db.transaction()
        if state_manager.move_planned_to_opened_transactional(transaction, pos_id):
            update_position_status(pos_id, "opened")

def check_cancellation_conditions(planned_pos: Dict[str, Any]) -> Optional[str]:
    """Sprawdza warunki anulowania dla zaplanowanej pozycji."""
    symbol = planned_pos["symbol"]
    direction = planned_pos["direction"]
    triggering_ob_timestamp = planned_pos.get("triggering_ob_timestamp")
    
    latest_ob_alerts = state_manager.get_last_orderblocks(symbol)
    if not latest_ob_alerts: return "Brak jakiegokolwiek aktualnego OrderBlocka dla symbolu."
    latest_ob = latest_ob_alerts[-1]
    if latest_ob.get("timestamp") != triggering_ob_timestamp:
        return f"OrderBlock (ts: {triggering_ob_timestamp}) został unieważniony przez nowy (ts: {latest_ob.get('timestamp')})."

    heatmap_event_type = "TOP_GREEN_CHANGE" if direction == "long" else "BOTTOM_RED_CHANGE"
    latest_heatmap_alerts = state_manager.get_last_heatmap(symbol, heatmap_event_type)
    if not latest_heatmap_alerts: return None

    current_heatmap_value_str = latest_heatmap_alerts[-1].get("value")
    if current_heatmap_value_str is None: return None

    try:
        current_heatmap_value = float(current_heatmap_value_str)
        ob_level_high = planned_pos["triggering_ob_level_high"]
        ob_level_low = planned_pos["triggering_ob_level_low"]

        if direction == "long" and not (ob_level_low < current_heatmap_value < ob_level_high):
            return f"Poziom TopGreen ({current_heatmap_value}) wyszedł poza granice pierwotnego OB ({ob_level_low} - {ob_level_high})."
        elif direction == "short" and not (ob_level_low < current_heatmap_value < ob_level_high):
            return f"Poziom BottomRed ({current_heatmap_value}) wyszedł poza granice pierwotnego OB ({ob_level_low} - {ob_level_high})."
    except (ValueError, TypeError, KeyError) as e:
        logger.error(f"[CANCEL_CHECK_ERROR] Błąd danych przy sprawdzaniu anulowania dla {planned_pos['position_id']}: {e}", exc_info=True)
        return f"Błąd danych (np. konwersji) podczas sprawdzania warunków anulowania."
    
    return None

def check_and_process_opened_position(opened_pos: Dict[str, Any], current_price: float):
    """Sprawdza jedną otwartą pozycję pod kątem zamknięcia na SL/TP."""
    pos_id = opened_pos["position_id"]
    direction = opened_pos["direction"]
    sl_price = opened_pos["stop_loss"]
    tp_price = opened_pos["take_profit"]
    db = get_db()

    closed_reason = None
    if direction == "long":
        if current_price <= sl_price: closed_reason = "SL_HIT"
        elif current_price >= tp_price: closed_reason = "TP_HIT"
    elif direction == "short":
        if current_price >= sl_price: closed_reason = "SL_HIT"
        elif current_price <= tp_price: closed_reason = "TP_HIT"
        
    if closed_reason:
        logger.info(f"[ZAMKNIĘCIE] {pos_id} przez {closed_reason} przy cenie {current_price}")
        transaction = db.transaction()
        if state_manager.remove_position_transactional(transaction, state_manager.OPENED_POSITIONS_COLLECTION, pos_id):
            update_position_status(pos_id, "closed", result_reason=closed_reason)