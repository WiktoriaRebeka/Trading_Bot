# TRADING_BOT/app/bot_logic.py

import logging
import requests
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta, timezone
import traceback # Na wszelki wypadek

# Poprawne importy
from firebase_admin import firestore # WAŻNE!
from .firebase_client import get_db
from . import state_manager
from .positions_logger import log_new_position, update_position_status
from .constants import (
    BYBIT_API_URL_V5_TICKERS,
    BYBIT_DEFAULT_CATEGORY,
    MAX_ALERT_AGE_SECONDS # Upewnij się, że ta stała jest poprawnie zdefiniowana w constants.py
)

logger = logging.getLogger(__name__) # Użyje konfiguracji loggera z app/main.py

# --- FUNKCJE POMOCNICZE ---

def get_all_prices_for_category(category: str = BYBIT_DEFAULT_CATEGORY) -> Dict[str, float]:
    """Pobiera ceny dla wszystkich symboli w danej kategorii za jednym zapytaniem."""
    logger.info(f"[GET_PRICES] Rozpoczynam pobieranie cen dla kategorii: {category}")
    params = {"category": category}
    headers = {'User-Agent': 'TradingBot/1.0 (AppEngine)', 'Accept': 'application/json'}
    all_prices = {}
    try:
        response = requests.get(BYBIT_API_URL_V5_TICKERS, params=params, headers=headers, timeout=15) # Zwiększony timeout
        logger.debug(f"[GET_PRICES] URL zapytania: {response.url}")
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
                        continue
            logger.info(f"[GET_PRICES] Pomyślnie pobrano ceny dla {len(all_prices)} symboli.")
            return all_prices
        else:
            logger.error(f"[GET_PRICES_API_ERROR] Błąd API Bybit: Code={data.get('retCode')}, Msg='{data.get('retMsg')}'. Odpowiedź: {data}")
            return {}
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"[GET_PRICES_HTTP_ERROR] Błąd HTTP: {http_err}. Odpowiedź serwera (fragment): {http_err.response.text[:200] if http_err.response else 'Brak odpowiedzi'}", exc_info=True)
    except requests.exceptions.RequestException as req_err:
        logger.error(f"[GET_PRICES_REQUEST_ERROR] Błąd żądania: {req_err}", exc_info=True)
    except Exception as e:
        logger.error(f"[GET_PRICES_UNEXPECTED_ERROR] Nieoczekiwany błąd: {e}", exc_info=True)
    return {}

# --- GŁÓWNA LOGIKA BOTA ---

def check_for_new_setups(symbol: str):
    """Sprawdza, czy najnowsze alerty tworzą prawidłowy, nowy setup do zaplanowania."""
    logger.info(f"--- [{symbol}] Rozpoczynam check_for_new_setups ---")
    last_ob_alert_q = state_manager.get_last_orderblocks(symbol)
    if not last_ob_alert_q:
        logger.info(f"[{symbol}] Brak alertów OrderBlock. Kończę.")
        return
    last_ob = last_ob_alert_q[-1] # Bierzemy najnowszy
    logger.info(f"[{symbol}] Ostatni OrderBlock (raw): {last_ob}")
    
    direction = last_ob.get("direction", "").lower()
    if not direction:
        logger.warning(f"[{symbol}] Brak 'direction' w OrderBlock. Alert OB: {last_ob}. Kończę.")
        return
    logger.info(f"[{symbol}] Kierunek z OB: '{direction}'")

    heatmap_event_type = "TOP_GREEN_CHANGE" if direction == "long" else "BOTTOM_RED_CHANGE"
    logger.info(f"[{symbol}] Oczekiwany typ heatmapy: '{heatmap_event_type}'")
    last_heatmap_alert_q = state_manager.get_last_heatmap(symbol, heatmap_event_type)
    if not last_heatmap_alert_q:
        logger.info(f"[{symbol}] Brak alertów Heatmap typu '{heatmap_event_type}'. Kończę.")
        return
    last_heatmap = last_heatmap_alert_q[-1] # Bierzemy najnowszy
    logger.info(f"[{symbol}] Ostatni alert Heatmap (raw): {last_heatmap}")

    try:
        # Walidacja i konwersja danych
        required_ob_fields = ["timestamp", "entry", "sl", "tp", "levelLow", "levelHigh"]
        for field in required_ob_fields:
            if field not in last_ob:
                logger.warning(f"[{symbol}] Brak kluczowego pola '{field}' w alercie OrderBlock: {last_ob}. Kończę.")
                return
        
        if "timestamp" not in last_heatmap or "value" not in last_heatmap:
            logger.warning(f"[{symbol}] Brak kluczowego pola 'timestamp' lub 'value' w alercie Heatmap: {last_heatmap}. Kończę.")
            return

        ob_ts_str = last_ob["timestamp"]
        heatmap_ts_str = last_heatmap["timestamp"]
            
        ob_ts = datetime.fromisoformat(ob_ts_str.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
        heatmap_ts = datetime.fromisoformat(heatmap_ts_str.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
        logger.info(f"[{symbol}][TIMESTAMPS] Parsowane: OB_ts={ob_ts.isoformat()}, Heatmap_ts={heatmap_ts.isoformat()}")

        time_diff_seconds = abs((ob_ts - heatmap_ts).total_seconds())
        logger.info(f"[{symbol}][TIME_DIFF] Różnica czasu między alertami: {time_diff_seconds:.2f}s (MAX_ALERT_AGE_SECONDS: {MAX_ALERT_AGE_SECONDS})")
        if time_diff_seconds > MAX_ALERT_AGE_SECONDS:
            logger.info(f"[{symbol}][TIME_DIFF] Alerty nie są wystarczająco blisko w czasie. Kończę.")
            return

        ob_entry = float(last_ob["entry"])
        ob_sl = float(last_ob["sl"])
        ob_tp = float(last_ob["tp"])
        heatmap_value = float(last_heatmap["value"])
        ob_level_low = float(last_ob["levelLow"]) # Zakładamy, że te pola są w alercie OB
        ob_level_high = float(last_ob["levelHigh"])
        logger.info(f"[{symbol}][VALUES_FLOAT] Parsowane: ob_entry={ob_entry}, ob_sl={ob_sl}, ob_tp={ob_tp}, heatmap_value={heatmap_value}, ob_low={ob_level_low}, ob_high={ob_level_high}")
        
        # Generowanie ID pozycji na podstawie timestampu OB dla unikalności
        position_id = state_manager.generate_position_id(symbol, direction, ob_entry, ob_ts_str) # Dodajemy ob_ts_str
        logger.info(f"[{symbol}][POS_ID] Wygenerowano: {position_id}")

        # Sprawdzenie, czy setup oparty na tym samym OB (identyfikowany przez jego timestamp) już istnieje
        planned_positions_for_symbol = state_manager.get_all_planned_for_symbol(symbol)
        for planned_pos in planned_positions_for_symbol:
            if planned_pos.get("triggering_ob_timestamp") == ob_ts_str:
                logger.info(f"[{symbol}][DUPLICATE_SETUP_CHECK] Setup oparty o ten sam OrderBlock (ts: {ob_ts_str}) już zaplanowany: {planned_pos.get('position_id')}. Pomijam.")
                return

        condition_met = False
        if direction == "long":
            # Warunek: SL < Heatmap < Entry
            logger.info(f"[{symbol}][CONDITION_CHECK_LONG] Sprawdzanie: ob_sl({ob_sl}) < heatmap_value({heatmap_value}) < ob_entry({ob_entry})")
            if ob_sl < heatmap_value < ob_entry:
                condition_met = True
        elif direction == "short":
            # Warunek: Entry < Heatmap < SL
            logger.info(f"[{symbol}][CONDITION_CHECK_SHORT] Sprawdzanie: ob_entry({ob_entry}) < heatmap_value({heatmap_value}) < ob_sl({ob_sl})")
            if ob_entry < heatmap_value < ob_sl:
                condition_met = True
        
        logger.info(f"[{symbol}][CONDITION_RESULT] Warunek setupu (condition_met): {condition_met}")
        
        if condition_met:
            position_details = {
                "position_id": position_id, "symbol": symbol, "direction": direction,
                "entry_price": ob_entry, "stop_loss": ob_sl, "take_profit": ob_tp,
                "status": "planned", 
                "planned_at": firestore.SERVER_TIMESTAMP, # WAŻNE: from firebase_admin import firestore na górze
                "triggering_ob_timestamp": ob_ts_str, 
                "triggering_ob_level_low": ob_level_low,
                "triggering_ob_level_high": ob_level_high,
                "triggering_heatmap_value_at_planning": heatmap_value
            }
            logger.info(f"[{symbol}][PLAN_DETAILS] Szczegóły planowanej pozycji: {position_details}")
            state_manager.add_planned_position(position_details) 
            log_new_position(symbol, direction, ob_entry, ob_sl, ob_tp, position_id) 
            logger.info(f"[✅ PLAN] {symbol} | Entry: {ob_entry} | SL: {ob_sl} | TP: {ob_tp} | Heatmap: {heatmap_value}")
        else:
            logger.info(f"[{symbol}][CONDITION_FAIL] Warunki setupu NIE spełnione.")

    except KeyError as ke:
        logger.error(f"[{symbol}][PLAN_ERROR_KEY] Brak kluczowego pola w danych alertu: {ke}. OB: {last_ob}, Heatmap: {last_heatmap}", exc_info=True)
    except ValueError as ve:
        logger.error(f"[{symbol}][PLAN_ERROR_VALUE] Błąd konwersji wartości na float: {ve}. OB: {last_ob}, Heatmap: {last_heatmap}", exc_info=True)
    except TypeError as te:
        logger.error(f"[{symbol}][PLAN_ERROR_TYPE] Błąd typu przy operacji: {te}. OB: {last_ob}, Heatmap: {last_heatmap}", exc_info=True)
    except Exception as e_general:
        logger.error(f"[{symbol}][PLAN_ERROR_GENERAL] Nieoczekiwany błąd w check_for_new_setups: {e_general}", exc_info=True)
    
    logger.info(f"--- [{symbol}] Zakończono check_for_new_setups ---")


def monitor_positions(all_prices: Dict[str, float]):
    logger.info(f"--- Rozpoczynam monitor_positions. Dostępne ceny dla {len(all_prices)} symboli. ---")
    all_symbols_with_positions = state_manager.get_all_position_symbols() # Ta funkcja pobiera z Firestore
    logger.info(f"[MONITOR_POS] Symbole z pozycjami (z Firestore) do sprawdzenia: {all_symbols_with_positions}")
    
    if not all_symbols_with_positions:
        logger.info("[MONITOR_POS] Brak symboli z pozycjami do monitorowania.")
        logger.info("--- Zakończono monitor_positions (brak pozycji) ---")
        return

    for symbol in all_symbols_with_positions:
        cleaned_symbol_for_price = symbol.replace(".P", "") 
        current_price = all_prices.get(cleaned_symbol_for_price)
        
        logger.info(f"== [{symbol}] Monitorowanie. Cena rynkowa ({cleaned_symbol_for_price}): {current_price} ==")
        
        if current_price is None:
            logger.warning(f"[{symbol}][MONITOR_POS] Brak aktualnej ceny dla {cleaned_symbol_for_price}. Pomijam.")
            continue
            
        planned_positions = state_manager.get_all_planned_for_symbol(symbol)
        logger.info(f"[{symbol}][MONITOR_POS] Znaleziono {len(planned_positions)} planowanych pozycji.")
        for pos_details in planned_positions:
            check_and_process_planned_position(pos_details, current_price) 
            
        opened_positions = state_manager.get_all_opened_for_symbol(symbol)
        logger.info(f"[{symbol}][MONITOR_POS] Znaleziono {len(opened_positions)} otwartych pozycji.")
        for pos_details in opened_positions:
            check_and_process_opened_position(pos_details, current_price)
            
    logger.info("--- Zakończono monitor_positions ---")


def check_and_process_planned_position(planned_pos_details: Dict[str, Any], current_price: float):
    pos_id = planned_pos_details.get("position_id", "UNKNOWN_PLANNED_ID") # Lepsza obsługa braku klucza
    symbol = planned_pos_details.get("symbol", "UNKNOWN_SYMBOL")
    direction = planned_pos_details.get("direction", "UNKNOWN_DIRECTION")
    
    try:
        entry_price = float(planned_pos_details["entry_price"])
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[{symbol}][PLANNED_CHECK_ERROR] {pos_id}: Błąd odczytu/konwersji entry_price: {e}. Dane pozycji: {planned_pos_details}", exc_info=True)
        return

    logger.info(f"[{symbol}][PLANNED_CHECK] Sprawdzam pozycję {pos_id}: Dir='{direction}', EntryPlan={entry_price}, CurrPrice={current_price}")
    db = get_db()

    cancel_reason = check_cancellation_conditions(planned_pos_details)
    if cancel_reason:
        logger.info(f"[{symbol}][PLANNED_CANCEL] Pozycja {pos_id} do anulowania. Powód: {cancel_reason}")
        transaction = db.transaction()
        if state_manager.remove_position_transactional(transaction, state_manager.PLANNED_POSITIONS_COLLECTION, pos_id):
            update_position_status(pos_id, "cancelled", result_reason=cancel_reason)
            logger.info(f"[{symbol}][PLANNED_CANCEL] Pozycja {pos_id} pomyślnie anulowana.")
        return 

    should_open = False
    if direction == "long":
        should_open = current_price >= entry_price
        logger.info(f"[{symbol}][PLANNED_OPEN_CHECK_LONG] {pos_id}: current_price({current_price}) >= entry_price({entry_price}) -> {should_open}")
    elif direction == "short":
        should_open = current_price <= entry_price
        logger.info(f"[{symbol}][PLANNED_OPEN_CHECK_SHORT] {pos_id}: current_price({current_price}) <= entry_price({entry_price}) -> {should_open}")
    else:
        logger.warning(f"[{symbol}][PLANNED_CHECK] {pos_id}: Nieznany kierunek '{direction}'. Nie można otworzyć.")


    if should_open:
        logger.info(f"[OTWARCIE] Aktywacja pozycji {pos_id} dla {symbol} przy cenie {current_price}")
        transaction = db.transaction()
        if state_manager.move_planned_to_opened_transactional(transaction, pos_id):
            update_position_status(pos_id, "opened")
            logger.info(f"[{symbol}][PLANNED_OPENED] Pozycja {pos_id} pomyślnie otwarta.")
    else:
        logger.info(f"[{symbol}][PLANNED_NO_OPEN] Dla {pos_id}: warunki otwarcia niespełnione (cena nie osiągnęła entry lub nieznany kierunek).")


def check_cancellation_conditions(planned_pos: Dict[str, Any]) -> Optional[str]:
    pos_id = planned_pos.get("position_id", "UNKNOWN_PLANNED_ID")
    symbol = planned_pos.get("symbol", "UNKNOWN_SYMBOL")
    direction = planned_pos.get("direction", "UNKNOWN_DIRECTION")
    triggering_ob_timestamp = planned_pos.get("triggering_ob_timestamp")
    
    logger.info(f"--- [{symbol}][CANCEL_CHECK] Sprawdzam warunki anulowania dla {pos_id} (OB ts: {triggering_ob_timestamp}) ---")

    latest_ob_alerts = state_manager.get_last_orderblocks(symbol)
    if not latest_ob_alerts:
        logger.info(f"[{symbol}][CANCEL_CHECK] {pos_id}: Brak aktualnego OrderBlocka dla symbolu. Powód do anulowania.")
        return "Brak aktualnego OrderBlocka dla symbolu."
    
    latest_ob = latest_ob_alerts[-1]
    logger.info(f"[{symbol}][CANCEL_CHECK] {pos_id}: Najnowszy OB (ts: {latest_ob.get('timestamp')}) vs Triggering OB (ts: {triggering_ob_timestamp})")
    if latest_ob.get("timestamp") != triggering_ob_timestamp:
        if latest_ob.get("direction", "").lower() != direction:
            reason = f"OrderBlock unieważniony przez nowy, PRZECIWSTAWNY OB (nowy ts: {latest_ob.get('timestamp')}, dir: {latest_ob.get('direction')})."
            logger.info(f"[{symbol}][CANCEL_CHECK] {pos_id}: {reason}")
            return reason
        else:
            # Jeśli kierunek jest ten sam, to niekoniecznie anulujemy, może być to tylko "odświeżenie" tego samego OB
            logger.info(f"[{symbol}][CANCEL_CHECK] {pos_id}: OrderBlock (ts: {triggering_ob_timestamp}) zmienił się na nowy (ts: {latest_ob.get('timestamp')}), ale kierunek taki sam. Kontynuuję sprawdzanie heatmapy.")
            # Można tu dodać logikę, czy nowy OB jest "gorszy" od starego, np. szerszy SL. Na razie nie anulujemy.


    heatmap_event_type = "TOP_GREEN_CHANGE" if direction == "long" else "BOTTOM_RED_CHANGE"
    latest_heatmap_alerts = state_manager.get_last_heatmap(symbol, heatmap_event_type)
    if not latest_heatmap_alerts:
        logger.info(f"[{symbol}][CANCEL_CHECK] {pos_id}: Brak alertów Heatmap typu '{heatmap_event_type}'. Brak powodu do anulowania na tej podstawie.")
        return None 

    current_heatmap_value_str = latest_heatmap_alerts[-1].get("value")
    if current_heatmap_value_str is None:
        logger.warning(f"[{symbol}][CANCEL_CHECK] {pos_id}: Brak wartości 'value' w ostatnim alercie heatmap. Nie można sprawdzić warunku heatmapy.")
        return None 

    try:
        current_heatmap_value = float(current_heatmap_value_str)
        # Te wartości są brane z `position_details` zapisanego podczas planowania
        triggering_ob_level_high = float(planned_pos["triggering_ob_level_high"]) 
        triggering_ob_level_low = float(planned_pos["triggering_ob_level_low"])
        logger.info(f"[{symbol}][CANCEL_CHECK_HEATMAP] {pos_id}: Aktualna heatmapa ({heatmap_event_type}): {current_heatmap_value}. Zakres pierwotnego OB: Low={triggering_ob_level_low}, High={triggering_ob_level_high}")

        # Zakładamy, że level_low to zawsze mniejsza wartość, a level_high większa,
        # a kierunek decyduje, czy to SL czy Entry.
        # Dla LONG: SL = level_low, Entry = level_high
        # Dla SHORT: Entry = level_low, SL = level_high
        # Warunek: Heatmap musi być MIĘDZY SL a Entry.
        
        # Uproszczony warunek: czy heatmap jest w zakresie [low, high] pierwotnego OB?
        if not (triggering_ob_level_low < current_heatmap_value < triggering_ob_level_high):
            reason = f"Poziom Heatmap ({heatmap_event_type}: {current_heatmap_value}) wyszedł poza granice pierwotnego OB ({triggering_ob_level_low} - {triggering_ob_level_high})."
            logger.info(f"[{symbol}][CANCEL_CHECK_HEATMAP] {pos_id}: {reason}")
            return reason

    except (ValueError, TypeError, KeyError) as e:
        logger.error(f"[{symbol}][CANCEL_CHECK_ERROR] {pos_id}: Błąd danych: {e}. Dane pozycji: {planned_pos}", exc_info=True)
        return f"Błąd danych (np. konwersji) podczas sprawdzania warunków anulowania heatmapy."
    
    logger.info(f"--- [{symbol}][CANCEL_CHECK] {pos_id}: Warunki anulowania NIESPEŁNIONE. ---")
    return None


def check_and_process_opened_position(opened_pos_details: Dict[str, Any], current_price: float):
    pos_id = opened_pos_details.get("position_id", "UNKNOWN_OPENED_ID")
    symbol = opened_pos_details.get("symbol", "UNKNOWN_SYMBOL")
    direction = opened_pos_details.get("direction", "UNKNOWN_DIRECTION")
    
    try:
        sl_price = float(opened_pos_details["stop_loss"])
        tp_price = float(opened_pos_details["take_profit"])
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[{symbol}][OPENED_CHECK_ERROR] {pos_id}: Błąd odczytu/konwersji SL/TP: {e}. Dane pozycji: {opened_pos_details}", exc_info=True)
        return

    logger.info(f"[{symbol}][OPENED_CHECK] Sprawdzam pozycję {pos_id}: Dir='{direction}', SL={sl_price}, TP={tp_price}, CurrPrice={current_price}")
    db = get_db()

    closed_reason = None
    if direction == "long":
        if current_price <= sl_price: 
            closed_reason = "SL_HIT"
            logger.info(f"[{symbol}][OPENED_SL_LONG] {pos_id}: current_price({current_price}) <= sl_price({sl_price})")
        elif current_price >= tp_price: 
            closed_reason = "TP_HIT"
            logger.info(f"[{symbol}][OPENED_TP_LONG] {pos_id}: current_price({current_price}) >= tp_price({tp_price})")
    elif direction == "short":
        if current_price >= sl_price: 
            closed_reason = "SL_HIT"
            logger.info(f"[{symbol}][OPENED_SL_SHORT] {pos_id}: current_price({current_price}) >= sl_price({sl_price})")
        elif current_price <= tp_price: 
            closed_reason = "TP_HIT"
            logger.info(f"[{symbol}][OPENED_TP_SHORT] {pos_id}: current_price({current_price}) <= tp_price({tp_price})")
    else:
        logger.warning(f"[{symbol}][OPENED_CHECK] {pos_id}: Nieznany kierunek '{direction}'. Nie można sprawdzić SL/TP.")
        
    if closed_reason:
        logger.info(f"[ZAMKNIĘCIE] Pozycja {pos_id} dla {symbol} zamknięta przez {closed_reason} przy cenie {current_price}")
        transaction = db.transaction()
        if state_manager.remove_position_transactional(transaction, state_manager.OPENED_POSITIONS_COLLECTION, pos_id):
            update_position_status(pos_id, "closed", result_reason=closed_reason, actual_close_price=current_price) # Dodaj actual_close_price
            logger.info(f"[{symbol}][OPENED_CLOSED] Pozycja {pos_id} pomyślnie zamknięta.")
    else:
        logger.info(f"[{symbol}][OPENED_NO_ACTION] Dla {pos_id}: brak akcji SL/TP.")