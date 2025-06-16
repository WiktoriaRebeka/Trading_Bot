# TRADING_BOT/app/bot_logic.py

import logging
import requests
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta, timezone
import time
import hmac
import hashlib
import json

# Poprawne importy
from firebase_admin import firestore
from .firebase_client import get_db
from . import state_manager
from .positions_logger import log_new_position, update_position_status
from .constants import (
    BYBIT_API_URL_V5_TICKERS,
    BYBIT_DEFAULT_CATEGORY,
    MAX_ALERT_AGE_SECONDS,
    BYBIT_API_KEY,
    BYBIT_API_SECRET
)

logger = logging.getLogger(__name__)

# --- FUNKCJA POMOCNICZA DO PARSOWANIA TIMESTAMPÓW ---
def parse_timestamp(ts_value):
    if isinstance(ts_value, datetime):
        return ts_value.astimezone(timezone.utc) if ts_value.tzinfo else ts_value.replace(tzinfo=timezone.utc)
    elif isinstance(ts_value, str):
        return datetime.fromisoformat(ts_value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
    raise TypeError(f"Nieobsługiwany typ dla timestamp: {type(ts_value)}")

# --- GŁÓWNE FUNKCJE LOGIKI BOTA ---

def get_all_prices_for_category(category: str = BYBIT_DEFAULT_CATEGORY) -> Dict[str, float]:
    """Pobiera ceny dla wszystkich symboli, używając kluczy API do autoryzacji."""
    logger.info(f"[GET_PRICES] Rozpoczynam pobieranie cen z autoryzacją dla kategorii: {category}")
    
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        logger.error("[GET_PRICES] Klucze API Bybit nie są skonfigurowane!")
        return {}

    timestamp = str(int(time.time() * 1000))
    recv_window = "10000"
    params = {"category": category}
    query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    sign_str = timestamp + BYBIT_API_KEY + recv_window + query_string
    signature = hmac.new(bytes(BYBIT_API_SECRET, "utf-8"), bytes(sign_str, "utf-8"), hashlib.sha256).hexdigest()
    headers = {
        'X-BAPI-API-KEY': BYBIT_API_KEY, 'X-BAPI-TIMESTAMP': timestamp,
        'X-BAPI-RECV-WINDOW': recv_window, 'X-BAPI-SIGN': signature,
    }
    
    all_prices = {}
    try:
        url = f"{BYBIT_API_URL_V5_TICKERS}?{query_string}"
        response = requests.get(url, headers=headers, timeout=15)
        logger.info(f"[GET_PRICES_RESPONSE] Status: {response.status_code}, Odpowiedź (fragment): {response.text[:500]}")
        response.raise_for_status()
        data = response.json()
        
        if data.get("retCode") == 0:
            if data.get("result") and data["result"].get("list"):
                for ticker in data["result"]["list"]:
                    symbol = ticker.get("symbol")
                    price_str = ticker.get("lastPrice")
                    if symbol and price_str:
                        try:
                            all_prices[symbol] = float(price_str)
                        except ValueError:
                            logger.warning(f"[GET_PRICES] Nie udało się sparsować ceny dla {symbol}: '{price_str}'")
                logger.info(f"[GET_PRICES] Pomyślnie pobrano ceny dla {len(all_prices)} symboli.")
            return all_prices
        else:
            logger.error(f"[GET_PRICES_API_ERROR] Błąd API Bybit: Code={data.get('retCode')}, Msg='{data.get('retMsg')}'")
            return {}
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"[GET_PRICES_HTTP_ERROR] Błąd HTTP: {http_err}.")
    except Exception as e:
        logger.error(f"[GET_PRICES_UNEXPECTED_ERROR] Nieoczekiwany błąd: {e}", exc_info=True)
    return {}

def process_new_alerts(newly_fetched_alerts: List[Dict]):
    """Przetwarza nowe alerty, aktualizując stan strategii."""
    logger.debug(f"Przetwarzam {len(newly_fetched_alerts)} nowych alertów w logice bota.")
    alerts_sorted = sorted(newly_fetched_alerts, key=lambda x: 0 if x.get('type') == 'OrderBlock' else 1)
    
    for alert in alerts_sorted:
        symbol = alert.get("symbol")
        if not symbol:
            continue
            
        if alert.get("type") == "OrderBlock":
            active_ob = state_manager.get_active_order_block(symbol)
            if active_ob and active_ob.get("timestamp") != alert.get("timestamp"):
                planned_positions = state_manager.get_all_planned_for_symbol(symbol)
                for pos in planned_positions:
                    if pos.get("triggering_ob_timestamp") == active_ob.get("timestamp"):
                        logger.info(f"[{symbol}] Nowy OB unieważnia starą planowaną pozycję: {pos.get('position_id')}. Anulowanie.")
                        db = get_db()
                        transaction = db.transaction()
                        if state_manager.remove_position_transactional(transaction, state_manager.PLANNED_POSITIONS_COLLECTION, pos.get('position_id')):
                            update_position_status(pos.get('position_id'), "cancelled", result_reason="New opposing OB")
            
            state_manager.set_active_order_block(symbol, alert)
        
        state_manager.process_alert(alert)

def run_strategy_cycle(all_active_symbols: List[str], all_prices: Dict[str, float]):
    """Główna pętla strategii, wywoływana w każdym cyklu bota."""
    logger.info("--- Rozpoczynam cykl strategii dla aktywnych symboli ---")
    
    for symbol in all_active_symbols:
        active_ob = state_manager.get_active_order_block(symbol)
        if not active_ob:
            logger.debug(f"[{symbol}] Brak aktywnego OB do analizy. Pomijam.")
            continue

        if state_manager.is_ob_mitigated(symbol):
            logger.debug(f"[{symbol}] Aktywny OB jest już zmitigowany. Pomijam.")
            continue

        current_price = all_prices.get(symbol.replace(".P", ""))
        if current_price is None:
            logger.warning(f"[{symbol}] Brak ceny rynkowej. Pomijam.")
            continue

        try:
            direction = active_ob.get("direction", "").lower()
            ob_level_low = float(active_ob["levelLow"])
            ob_level_high = float(active_ob["levelHigh"])
            entry_price = float(active_ob["entry"])
            ob_sl_price = float(active_ob["sl"])
            
            # WARUNEK 1: MITIGACJA PRZEZ CENĘ
            is_mitigated_by_price = False
            if direction == "long" and current_price <= ob_level_high:
                is_mitigated_by_price = True
            elif direction == "short" and current_price >= ob_level_low:
                is_mitigated_by_price = True

            if is_mitigated_by_price:
                state_manager.set_ob_as_mitigated(symbol)
                logger.info(f"[{symbol}] Aktywny OB zmitigowany przez cenę rynkową. Staje się nieaktywny.")
                continue

            # WARUNEK 2: PLANOWANIE (jeśli nie zmitigowano)
            heatmap_event_type = "TOP_GREEN_CHANGE" if direction == "long" else "BOTTOM_RED_CHANGE"
            heatmap_alerts = state_manager.get_last_heatmap(symbol, heatmap_event_type)
            if not heatmap_alerts:
                continue

            last_heatmap = heatmap_alerts[-1]
            heatmap_value = float(last_heatmap["value"])
            
            is_in_zone = (ob_sl_price < heatmap_value < entry_price) if direction == "long" else (entry_price < heatmap_value < ob_sl_price)
            
            if is_in_zone:
                triggering_ob_timestamp_str = active_ob["timestamp"] if isinstance(active_ob["timestamp"], str) else active_ob["timestamp"].isoformat()
                position_id = state_manager.generate_position_id(symbol, direction, entry_price, triggering_ob_timestamp_str)
                is_already_planned = any(p.get("position_id") == position_id for p in state_manager.get_all_planned_for_symbol(symbol))

                if not is_already_planned:
                    logger.info(f"[✅ PLAN] {symbol} | Warunki spełnione. Planowanie pozycji.")
                    position_details = {
                        "position_id": position_id, "symbol": symbol, "direction": direction,
                        "entry_price": entry_price, "stop_loss": ob_sl_price, "take_profit": float(active_ob["tp"]), "status": "planned", 
                        "planned_at": firestore.SERVER_TIMESTAMP, "triggering_ob_timestamp": triggering_ob_timestamp_str,
                        "triggering_ob_level_low": ob_level_low, "triggering_ob_level_high": ob_level_high,
                        "triggering_heatmap_value_at_planning": heatmap_value
                    }
                    state_manager.add_planned_position(position_details)
                    log_new_position(symbol, direction, entry_price, ob_sl_price, float(active_ob["tp"]), position_id)
        
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"[{symbol}] Błąd w cyklu strategii: {e}", exc_info=True)
            
def monitor_positions(all_prices: Dict[str, float]):
    logger.info(f"--- Rozpoczynam monitor_positions ---")
    all_symbols_with_positions = state_manager.get_all_position_symbols()
    
    if not all_symbols_with_positions:
        logger.info("[MONITOR_POS] Brak pozycji do monitorowania.")
        return
        
    logger.debug(f"[MONITOR_POS] Symbole z pozycjami do sprawdzenia: {all_symbols_with_positions}")

    for symbol in all_symbols_with_positions:
        cleaned_symbol_for_price = symbol.replace(".P", "") 
        current_price = all_prices.get(cleaned_symbol_for_price)
        
        if current_price is None:
            logger.warning(f"[{symbol}][MONITOR_POS] Brak aktualnej ceny dla {cleaned_symbol_for_price}.")
            continue
            
        logger.debug(f"== [{symbol}] Monitorowanie. Cena rynkowa: {current_price} ==")
        
        planned_positions = state_manager.get_all_planned_for_symbol(symbol)
        for pos_details in planned_positions:
            check_and_process_planned_position(pos_details, current_price) 
            
        opened_positions = state_manager.get_all_opened_for_symbol(symbol)
        for pos_details in opened_positions:
            check_and_process_opened_position(pos_details, current_price)
            
    logger.info("--- Zakończono monitor_positions ---")


def check_and_process_planned_position(planned_pos_details: Dict[str, Any], current_price: float):
    pos_id = planned_pos_details.get("position_id", "UNKNOWN_PLANNED_ID")
    symbol = planned_pos_details.get("symbol", "UNKNOWN_SYMBOL")
    
    logger.debug(f"[{symbol}][PLANNED_CHECK] Sprawdzam pozycję {pos_id}")
    
    cancel_reason = check_cancellation_conditions(planned_pos_details)
    if cancel_reason:
        logger.info(f"[{symbol}][PLANNED_CANCEL] Anulowanie pozycji {pos_id}. Powód: {cancel_reason}")
        db = get_db()
        transaction = db.transaction()
        if state_manager.remove_position_transactional(transaction, state_manager.PLANNED_POSITIONS_COLLECTION, pos_id):
            update_position_status(pos_id, "cancelled", result_reason=cancel_reason)
        return 

    try:
        direction = planned_pos_details["direction"]
        entry_price = float(planned_pos_details["entry_price"])
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[{symbol}][PLANNED_CHECK_ERROR] {pos_id}: Błąd odczytu/konwersji danych: {e}.", exc_info=True)
        return

    should_open = False
    if direction == "long" and current_price >= entry_price:
        should_open = True
    elif direction == "short" and current_price <= entry_price:
        should_open = True

    if should_open:
        logger.info(f"[OTWARCIE] Aktywacja pozycji {pos_id} dla {symbol} przy cenie {current_price}")
        db = get_db()
        transaction = db.transaction()
        if state_manager.move_planned_to_opened_transactional(transaction, pos_id):
            update_position_status(pos_id, "opened")


def check_cancellation_conditions(planned_pos: Dict[str, Any]) -> Optional[str]:
    pos_id = planned_pos.get("position_id", "UNKNOWN_PLANNED_ID")
    symbol = planned_pos.get("symbol", "UNKNOWN_SYMBOL")
    direction = planned_pos.get("direction", "UNKNOWN_DIRECTION")
    triggering_ob_timestamp = planned_pos.get("triggering_ob_timestamp")
    
    logger.debug(f"--- [{symbol}][CANCEL_CHECK] Sprawdzam warunki anulowania dla {pos_id} ---")

    latest_ob_alerts = state_manager.get_last_orderblocks(symbol)
    if latest_ob_alerts:
        latest_ob = latest_ob_alerts[-1]
        latest_ob_ts_value = latest_ob.get("timestamp")
        if latest_ob_ts_value:
            latest_ob_ts_str = latest_ob_ts_value if isinstance(latest_ob_ts_value, str) else latest_ob_ts_value.isoformat()
            if latest_ob_ts_str != triggering_ob_timestamp and latest_ob.get("direction", "").lower() != direction:
                return f"Unieważniono przez nowy, PRZECIWSTAWNY OB (nowy dir: {latest_ob.get('direction')})."

    heatmap_event_type = "TOP_GREEN_CHANGE" if direction == "long" else "BOTTOM_RED_CHANGE"
    latest_heatmap_alerts = state_manager.get_last_heatmap(symbol, heatmap_event_type)
    if not latest_heatmap_alerts:
        return None

    try:
        current_heatmap_value = float(latest_heatmap_alerts[-1].get("value"))
        triggering_ob_level_high = float(planned_pos["triggering_ob_level_high"]) 
        triggering_ob_level_low = float(planned_pos["triggering_ob_level_low"])
        
        if not (triggering_ob_level_low < current_heatmap_value < triggering_ob_level_high):
            return f"Poziom Heatmap ({current_heatmap_value}) wyszedł poza granice OB ({triggering_ob_level_low} - {triggering_ob_level_high})."
    except (ValueError, TypeError, KeyError, IndexError) as e:
        logger.error(f"[{symbol}][CANCEL_CHECK_ERROR] {pos_id}: Błąd danych: {e}.", exc_info=True)
        return f"Błąd danych podczas sprawdzania warunków anulowania."
    
    return None


def check_and_process_opened_position(opened_pos_details: Dict[str, Any], current_price: float):
    pos_id = opened_pos_details.get("position_id", "UNKNOWN_OPENED_ID")
    symbol = opened_pos_details.get("symbol", "UNKNOWN_SYMBOL")

    try:
        direction = opened_pos_details["direction"]
        sl_price = float(opened_pos_details["stop_loss"])
        tp_price = float(opened_pos_details["take_profit"])
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[{symbol}][OPENED_CHECK_ERROR] {pos_id}: Błąd odczytu/konwersji danych: {e}.", exc_info=True)
        return

    logger.debug(f"[{symbol}][OPENED_CHECK] Sprawdzam {pos_id}: Dir='{direction}', SL={sl_price}, TP={tp_price}, Cena={current_price}")

    closed_reason = None
    if direction == "long":
        if current_price <= sl_price: closed_reason = "SL_HIT"
        elif current_price >= tp_price: closed_reason = "TP_HIT"
    elif direction == "short":
        if current_price >= sl_price: closed_reason = "SL_HIT"
        elif current_price <= tp_price: closed_reason = "TP_HIT"
        
    if closed_reason:
        logger.info(f"[ZAMKNIĘCIE] Pozycja {pos_id} dla {symbol} zamknięta przez {closed_reason} przy cenie {current_price}")
        db = get_db()
        transaction = db.transaction()
        if state_manager.remove_position_transactional(transaction, state_manager.OPENED_POSITIONS_COLLECTION, pos_id):
            update_position_status(pos_id, "closed", result_reason=closed_reason, actual_close_price=current_price)