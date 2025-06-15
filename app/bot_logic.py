# TRADING_BOT/app/bot_logic.py

import logging
import requests
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta, timezone
# NOWE IMPORTY POTRZEBNE DO PODPISYWANIA
import time
import hmac
import hashlib

# Poprawne importy
from firebase_admin import firestore
from .firebase_client import get_db
from . import state_manager
from .positions_logger import log_new_position, update_position_status
from .constants import (
    BYBIT_API_URL_V5_TICKERS,
    BYBIT_DEFAULT_CATEGORY,
    MAX_ALERT_AGE_SECONDS,
    # IMPORTUJEMY KLUCZE
    BYBIT_API_KEY,
    BYBIT_API_SECRET
)

logger = logging.getLogger(__name__)

# --- FUNKCJE POMOCNICZE ---

def get_all_prices_for_category(category: str = BYBIT_DEFAULT_CATEGORY) -> Dict[str, float]:
    """Pobiera ceny dla wszystkich symboli, używając kluczy API do autoryzacji."""
    logger.info(f"[GET_PRICES] Rozpoczynam pobieranie cen z autoryzacją dla kategorii: {category}")
    
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        logger.error("[GET_PRICES] Klucze API Bybit nie są skonfigurowane! Nie można pobrać cen.")
        return {}

    # 1. Przygotuj parametry zapytania (query string)
    params = {"category": category}
    query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])

    # 2. Przygotuj dane do podpisania (timestamp, klucz api, okno odbioru, query string)
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    sign_str = timestamp + BYBIT_API_KEY + recv_window + query_string

    # 3. Wygeneruj podpis
    signature = hmac.new(
        bytes(BYBIT_API_SECRET, "utf-8"),
        bytes(sign_str, "utf-8"),
        hashlib.sha256
    ).hexdigest()

    # 4. Przygotuj nagłówki z podpisem
    headers = {
        'X-BAPI-API-KEY': BYBIT_API_KEY,
        'X-BAPI-TIMESTAMP': timestamp,
        'X-BAPI-RECV-WINDOW': recv_window,
        'X-BAPI-SIGN': signature,
        'Content-Type': 'application/json'
    }

    # 5. Wykonaj zapytanie
    all_prices = {}
    try:
        response = requests.get(BYBIT_API_URL_V5_TICKERS, params=params, headers=headers, timeout=15)
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
                        logger.warning(f"[GET_PRICES] Nie udało się sparsować ceny dla {symbol}: '{price_str}'")
            logger.info(f"[GET_PRICES] Pomyślnie pobrano ceny dla {len(all_prices)} symboli.")
            return all_prices
        else:
            logger.error(f"[GET_PRICES_API_ERROR] Błąd API Bybit: Code={data.get('retCode')}, Msg='{data.get('retMsg')}'")
            return {}
            
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"[GET_PRICES_HTTP_ERROR] Błąd HTTP: {http_err}.", exc_info=True)
    except Exception as e:
        logger.error(f"[GET_PRICES_UNEXPECTED_ERROR] Nieoczekiwany błąd: {e}", exc_info=True)
        
    return {}
# --- GŁÓWNA LOGIKA BOTA ---

def check_for_new_setups(symbol: str):
    """Sprawdza, czy najnowsze alerty tworzą prawidłowy, nowy setup do zaplanowania."""
    logger.info(f"--- [{symbol}] Rozpoczynam check_for_new_setups ---")
    last_ob_alert_q = state_manager.get_last_orderblocks(symbol)
    if not last_ob_alert_q:
        logger.debug(f"[{symbol}] Brak alertów OrderBlock w pamięci. Kończę.")
        return
    last_ob = last_ob_alert_q[-1]
    
    direction = last_ob.get("direction", "").lower()
    if not direction:
        logger.warning(f"[{symbol}] Brak 'direction' w alercie OrderBlock: {last_ob}. Kończę.")
        return

    heatmap_event_type = "TOP_GREEN_CHANGE" if direction == "long" else "BOTTOM_RED_CHANGE"
    last_heatmap_alert_q = state_manager.get_last_heatmap(symbol, heatmap_event_type)
    if not last_heatmap_alert_q:
        logger.debug(f"[{symbol}] Brak pasujących alertów Heatmap typu '{heatmap_event_type}'. Kończę.")
        return
    last_heatmap = last_heatmap_alert_q[-1]

    try:
        # === POPRAWIONA, ODPORNA LOGIKA PARSOWANIA TIMESTAMPÓW ===
        ob_ts_value = last_ob.get("timestamp")
        heatmap_ts_value = last_heatmap.get("timestamp")
        
        if not ob_ts_value or not heatmap_ts_value:
             logger.warning(f"[{symbol}] Brak pola 'timestamp' w jednym z alertów. OB: {ob_ts_value}, Heatmap: {heatmap_ts_value}. Kończę.")
             return

        def parse_timestamp(ts_value):
            if isinstance(ts_value, datetime):
                return ts_value.astimezone(timezone.utc) if ts_value.tzinfo else ts_value.replace(tzinfo=timezone.utc)
            elif isinstance(ts_value, str):
                return datetime.fromisoformat(ts_value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
            raise TypeError(f"Nieobsługiwany typ dla timestamp: {type(ts_value)}")

        ob_ts = parse_timestamp(ob_ts_value)
        heatmap_ts = parse_timestamp(heatmap_ts_value)
        
        triggering_ob_timestamp_str = ob_ts_value if isinstance(ob_ts_value, str) else ob_ts_value.isoformat()
        
        logger.debug(f"[{symbol}][TIMESTAMPS] Parsowane: OB_ts={ob_ts.isoformat()}, Heatmap_ts={heatmap_ts.isoformat()}")

        time_diff_seconds = abs((ob_ts - heatmap_ts).total_seconds())
        logger.debug(f"[{symbol}][TIME_DIFF] Różnica czasu: {time_diff_seconds:.2f}s (MAX: {MAX_ALERT_AGE_SECONDS}s)")
        if time_diff_seconds > MAX_ALERT_AGE_SECONDS:
            logger.info(f"[{symbol}] Setup odrzucony: alerty zbyt odległe w czasie.")
            return

        # --- Koniec logiki parsowania timestampów ---
        
        ob_entry = float(last_ob["entry"])
        ob_sl = float(last_ob["sl"])
        ob_tp = float(last_ob["tp"])
        heatmap_value = float(last_heatmap["value"])
        ob_level_low = float(last_ob["levelLow"])
        ob_level_high = float(last_ob["levelHigh"])
        
        position_id = state_manager.generate_position_id(symbol, direction, ob_entry, triggering_ob_timestamp_str)
        
        planned_positions_for_symbol = state_manager.get_all_planned_for_symbol(symbol)
        for planned_pos in planned_positions_for_symbol:
            if planned_pos.get("triggering_ob_timestamp") == triggering_ob_timestamp_str:
                logger.info(f"[{symbol}] Setup odrzucony: pozycja oparta o ten sam OB (ts: {triggering_ob_timestamp_str}) już istnieje: {planned_pos.get('position_id')}.")
                return

        condition_met = False
        if direction == "long" and ob_sl < heatmap_value < ob_entry:
            condition_met = True
        elif direction == "short" and ob_entry < heatmap_value < ob_sl:
            condition_met = True
        
        logger.info(f"[{symbol}][CONDITION_RESULT] Wynik sprawdzenia warunku ceny: {condition_met}")
        
        if condition_met:
            position_details = {
                "position_id": position_id, "symbol": symbol, "direction": direction,
                "entry_price": ob_entry, "stop_loss": ob_sl, "take_profit": ob_tp,
                "status": "planned", 
                "planned_at": firestore.SERVER_TIMESTAMP,
                "triggering_ob_timestamp": triggering_ob_timestamp_str,
                "triggering_ob_level_low": ob_level_low,
                "triggering_ob_level_high": ob_level_high,
                "triggering_heatmap_value_at_planning": heatmap_value
            }
            state_manager.add_planned_position(position_details) 
            log_new_position(symbol, direction, ob_entry, ob_sl, ob_tp, position_id) 
            logger.info(f"[✅ PLAN] {symbol} | Entry: {ob_entry} | SL: {ob_sl} | TP: {ob_tp} | Heatmap: {heatmap_value}")

    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[{symbol}][PLAN_ERROR] Błąd danych, konwersji lub typu: {e}. OB: {last_ob}, Heatmap: {last_heatmap}", exc_info=True)
    except Exception as e_general:
        logger.error(f"[{symbol}][PLAN_ERROR_GENERAL] Nieoczekiwany błąd w check_for_new_setups: {e_general}", exc_info=True)
    
    logger.info(f"--- [{symbol}] Zakończono check_for_new_setups ---")


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
            logger.warning(f"[{symbol}][MONITOR_POS] Brak aktualnej ceny dla {cleaned_symbol_for_price}. Pomijam monitorowanie tego symbolu.")
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