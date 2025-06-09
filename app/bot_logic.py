# TRADING_BOT/app/bot_logic.py
import requests
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import traceback
import logging # <--- DODANE

# Importy z Twojego projektu
from app import state_manager
from app.positions_logger import log_new_position, update_position_status
from app.constants import (
    BYBIT_API_URL_V5_TICKERS,
    BYBIT_DEFAULT_CATEGORY,
    MAX_ALERT_AGE_SECONDS
)

# Inicjalizacja loggera dla tego modułu
logger = logging.getLogger(__name__) # <--- DODANE

def get_bybit_compatible_symbol(tv_symbol: str) -> str:
    """Konwertuje symbol z TradingView na format akceptowany przez Bybit API V5."""
    if tv_symbol.endswith(".P"):
        return tv_symbol[:-2]
    return tv_symbol

def get_current_price(raw_symbol: str) -> Optional[float]:
    cleaned_symbol = get_bybit_compatible_symbol(raw_symbol)
    params = {"category": BYBIT_DEFAULT_CATEGORY, "symbol": cleaned_symbol}
    # Pamiętaj, aby zaktualizować User-Agent na Twój właściwy kontakt/URL projektu
    headers = {
        'User-Agent': 'TradingBot/1.0 (Python Requests; AppEngine; +https://twoj-projekt-url.com)', 
        'Accept': 'application/json'
    }
    
    logger.debug(f"[PRICE_DEBUG] Próba pobrania ceny dla {cleaned_symbol} z URL: {BYBIT_API_URL_V5_TICKERS} z params: {params} i headers: {headers}")

    try:
        response = requests.get(BYBIT_API_URL_V5_TICKERS, params=params, headers=headers, timeout=10)
        logger.debug(f"[PRICE_DEBUG] Odpowiedź dla {cleaned_symbol} - Status: {response.status_code}, Nagłówki (fragment): {str(response.headers)[:200]}...")
        # logger.debug(f"[PRICE_DEBUG_RAW_CONTENT] Surowa treść odpowiedzi dla {cleaned_symbol} (pierwsze 500 znaków): {response.text[:500]}") # Odkomentuj w razie potrzeby głębszego debugowania

        response.raise_for_status()
        data = response.json()

        if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
            tickers_list = data["result"]["list"]
            if tickers_list:
                price_str = tickers_list[0].get('lastPrice')
                if price_str is not None:
                    try:
                        price = float(price_str)
                        logger.info(f"[PRICE_INFO] Pobrana cena dla {cleaned_symbol}: {price}")
                        return price
                    except ValueError:
                        logger.error(f"[PRICE_ERROR] get_current_price({cleaned_symbol}): Nie można przekonwertować 'lastPrice' na float: '{price_str}'. Odpowiedź API: {data}")
                        return None
                else:
                    logger.error(f"[PRICE_ERROR] get_current_price({cleaned_symbol}): Brak 'lastPrice' w odpowiedzi. Odpowiedź API: {data}")
                    return None
            else:
                logger.error(f"[PRICE_ERROR] get_current_price({cleaned_symbol}): Lista tickerów pusta. Odpowiedź API: {data}")
                return None
        else:
            ret_msg = data.get('retMsg', 'Brak wiadomości zwrotnej')
            ret_code = data.get('retCode', 'Brak kodu zwrotnego')
            logger.error(f"[PRICE_ERROR] get_current_price({cleaned_symbol}): Niepoprawna odpowiedź API Bybit - Kod: {ret_code}, Wiadomość: '{ret_msg}'. Pełna odpowiedź: {data}")
            return None

    except requests.exceptions.HTTPError as http_err:
        response_text_snippet = "Brak response.text"
        if http_err.response is not None:
            response_text_snippet = http_err.response.text[:500]
        logger.error(f"[PRICE_ERROR] HTTP dla {cleaned_symbol}: Status '{http_err.response.status_code if http_err.response else 'N/A'}'. URL: {http_err.request.url if hasattr(http_err, 'request') and http_err.request else 'N/A'}. Odpowiedź (fragment): '{response_text_snippet}'. Błąd: {http_err}", exc_info=True)
        return None
    except requests.exceptions.Timeout:
        logger.error(f"[PRICE_ERROR] Timeout (po 10s) podczas połączenia z Bybit dla {cleaned_symbol} (URL: {BYBIT_API_URL_V5_TICKERS}, Params: {params})", exc_info=True)
        return None
    except requests.exceptions.ConnectionError as conn_err:
        logger.error(f"[PRICE_ERROR] ConnectionError dla {cleaned_symbol}: {conn_err} (URL: {BYBIT_API_URL_V5_TICKERS}, Params: {params})", exc_info=True)
        return None
    except requests.exceptions.RequestException as req_err:
        logger.error(f"[PRICE_ERROR] Inny RequestException dla {cleaned_symbol}: {req_err} (URL: {BYBIT_API_URL_V5_TICKERS}, Params: {params})", exc_info=True)
        return None
    except (KeyError, IndexError, ValueError, TypeError) as e:
        logger.error(f"[PRICE_ERROR] Parsowania/Przetwarzania dla {cleaned_symbol}: Błąd przetwarzania odpowiedzi API - {type(e).__name__}: {e}.", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"[PRICE_ERROR] Ogólny nieoczekiwany błąd w get_current_price dla {cleaned_symbol}: {type(e).__name__}: {e}", exc_info=True)
        return None

def is_alert_recent(alert: dict, max_age_sec: int = MAX_ALERT_AGE_SECONDS) -> bool:
    ts_str = alert.get("timestamp") or alert.get("received_at")
    if not ts_str:
        logger.warning(f"[ALERT_TIME_WARN] Brak timestampu lub received_at w alercie: {alert.get('id', 'N/A')}")
        return False
    try:
        alert_time = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00")) # Upewniamy się, że ts_str jest stringiem
        if alert_time.tzinfo is None:
             alert_time = alert_time.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - alert_time < timedelta(seconds=max_age_sec)
    except ValueError:
        logger.error(f"[ALERT_TIME_ERROR] Nieprawidłowy format timestampu: {ts_str} dla alertu: {alert.get('id', 'N/A')}")
        return False

def _check_and_plan_position(symbol: str, direction: str):
    logger.debug(f"[_CHECK_PLAN_DEBUG] Sprawdzanie planowania dla {symbol}, kierunek: {direction}")
    ob_alerts = state_manager.get_last_orderblocks(symbol)
    if not ob_alerts:
        logger.debug(f"[_CHECK_PLAN_DEBUG] Brak OrderBlocków dla {symbol}.")
        return
    
    last_ob = ob_alerts[-1]
    if last_ob.get("direction", "").lower() != direction.lower():
        logger.debug(f"[_CHECK_PLAN_DEBUG] Kierunek ostatniego OB ({last_ob.get('direction')}) niezgodny z oczekiwanym ({direction}) dla {symbol}.")
        return
    if not is_alert_recent(last_ob):
        logger.debug(f"[_CHECK_PLAN_DEBUG] OrderBlock dla {symbol} (ts: {last_ob.get('timestamp')}) nieświeży.")
        return

    heatmap_event_type = "TOP_GREEN_CHANGE" if direction.lower() == "long" else "BOTTOM_RED_CHANGE"
    heatmap_alerts = state_manager.get_last_heatmap(symbol, heatmap_event_type)
    if not heatmap_alerts:
        logger.debug(f"[_CHECK_PLAN_DEBUG] Brak alertów heatmap typu '{heatmap_event_type}' dla {symbol}.")
        return
    
    last_heatmap = heatmap_alerts[-1]
    if not is_alert_recent(last_heatmap):
        logger.debug(f"[_CHECK_PLAN_DEBUG] Alert Heatmap dla {symbol} (ts: {last_heatmap.get('timestamp')}, typ: {heatmap_event_type}) nieświeży.")
        return

    try:
        ob_entry = float(last_ob["entry"])
        ob_sl = float(last_ob["sl"])
        ob_tp = float(last_ob["tp"])
        heatmap_value = float(last_heatmap["value"])
        ob_timestamp = last_ob.get("timestamp")
        ob_level_low = float(last_ob.get("levelLow", 0)) # Użyj 0 jako domyślnej, jeśli brakuje
        ob_level_high = float(last_ob.get("levelHigh", 0))# Użyj 0 jako domyślnej, jeśli brakuje
    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[{direction.upper()}_PLAN_ERROR] Brak kluczowych pól lub błąd konwersji w alertach dla {symbol}: {type(e).__name__}: {e}. Alerty: OB={last_ob}, Heatmap={last_heatmap}", exc_info=True)
        return

    condition_met = False
    if direction.lower() == "long" and ob_sl < heatmap_value < ob_entry:
        condition_met = True
    elif direction.lower() == "short" and ob_entry < heatmap_value < ob_sl:
        condition_met = True
            
    if condition_met:
        for planned_pos_details in state_manager.get_all_planned_positions_for_symbol(symbol):
            if (planned_pos_details.get("triggering_ob_timestamp") == ob_timestamp and
                planned_pos_details.get("direction") == direction.lower()):
                logger.debug(f"[{direction.upper()}_PLAN_DEBUG] Pozycja dla {symbol} oparta o ten sam OB ({ob_timestamp}) już zaplanowana.")
                return

        planned_at_iso = datetime.now(timezone.utc).isoformat()
        position_id = state_manager.generate_position_id(symbol, direction.lower(), ob_entry, planned_at_iso)
        
        position_details = {
            "position_id": position_id, "symbol": symbol, "direction": direction.lower(),
            "entry_price": ob_entry, "stop_loss": ob_sl, "take_profit": ob_tp,
            "status": "planned", "planned_at": planned_at_iso,
            "triggering_ob_timestamp": ob_timestamp, "triggering_ob_level_low": ob_level_low,
            "triggering_ob_level_high": ob_level_high, "triggering_heatmap_value_at_planning": heatmap_value,
        }
        state_manager.add_planned_position(position_id, position_details)
        log_new_position(symbol, direction.lower(), ob_entry, ob_sl, ob_tp, position_id) # Zakładamy, że ta funkcja używa swojego loggera lub print
        logger.info(f"[✅ {direction.upper()}_PLAN] {symbol} | Entry: {ob_entry} | SL: {ob_sl} | TP: {ob_tp} | Heatmap: {heatmap_value}")
    else:
        logger.debug(f"[⛔ {direction.upper()}_PLAN_COND_FALSE] {symbol} | OB Entry: {ob_entry}, SL: {ob_sl} | Heatmap: {heatmap_value} | Direction: {direction}")

def check_new_long_entries(symbol: str):
    _check_and_plan_position(symbol, "long")

def check_new_short_entries(symbol: str):
    _check_and_plan_position(symbol, "short")

def monitor_planned_positions(symbol: str):
    planned_positions_for_symbol = list(state_manager.get_all_planned_positions_for_symbol(symbol))
    if not planned_positions_for_symbol: return

    current_price = get_current_price(symbol)
    if current_price is None:
        logger.error(f"[MONITOR_PLANNED_ERROR] Brak ceny dla {symbol}, pomijam monitorowanie planowanych.")
        return

    latest_ob_alerts = state_manager.get_last_orderblocks(symbol)
    latest_ob = latest_ob_alerts[-1] if latest_ob_alerts else None

    for planned_pos in planned_positions_for_symbol:
        pos_id = planned_pos["position_id"]
        direction = planned_pos["direction"]
        cancel_reason = None

        if latest_ob and latest_ob.get("timestamp") != planned_pos.get("triggering_ob_timestamp"):
            cancel_reason = f"OrderBlock (ts: {planned_pos.get('triggering_ob_timestamp')}) zmienił się na nowy (ts: {latest_ob.get('timestamp')})."
        elif not latest_ob:
            cancel_reason = "Brak aktualnego OrderBlocka dla symbolu."

        if not cancel_reason:
            heatmap_event_type = "TOP_GREEN_CHANGE" if direction == "long" else "BOTTOM_RED_CHANGE"
            latest_heatmap_alerts = state_manager.get_last_heatmap(symbol, heatmap_event_type)
            if latest_heatmap_alerts:
                current_heatmap_value_str = latest_heatmap_alerts[-1].get("value")
                if current_heatmap_value_str is not None:
                    try:
                        current_heatmap_value = float(current_heatmap_value_str)
                        entry_at_planning = float(planned_pos["entry_price"])
                        sl_at_planning = float(planned_pos["stop_loss"])
                        if direction == "long" and not (sl_at_planning < current_heatmap_value < entry_at_planning):
                            cancel_reason = f"Heatmap TOP_GREEN ({current_heatmap_value}) wyszedł poza zakres pierwotnego OB ({sl_at_planning} - {entry_at_planning})."
                        elif direction == "short" and not (entry_at_planning < current_heatmap_value < sl_at_planning):
                            cancel_reason = f"Heatmap BOTTOM_RED ({current_heatmap_value}) wyszedł poza zakres pierwotnego OB ({entry_at_planning} - {sl_at_planning})."
                    except (ValueError, TypeError) as e:
                        logger.error(f"[MONITOR_PLANNED_ERROR] Błąd konwersji wartości dla {pos_id}: {e}", exc_info=True)
                        cancel_reason = f"Błąd danych heatmapy lub pozycji ({type(e).__name__})."
                else:
                    logger.warning(f"[MONITOR_PLANNED_WARN] Brak wartości 'value' w ostatnim alercie heatmap dla {pos_id}")
        
        if cancel_reason:
            logger.info(f"[ANULOWANIE] {pos_id} dla {symbol}: {cancel_reason}")
            state_manager.remove_planned_position(pos_id)
            update_position_status(pos_id, "cancelled", result_reason=cancel_reason)
            continue 

        entry_price_planned = float(planned_pos["entry_price"])
        opened = False
        if direction == "long" and current_price >= entry_price_planned:
            opened = True
        elif direction == "short" and current_price <= entry_price_planned:
            opened = True
        
        if opened:
            logger.info(f"[OTWARCIE {direction.upper()}] {pos_id} dla {symbol} przy cenie {current_price} (Planowane Entry: {entry_price_planned})")
            if state_manager.move_planned_to_opened(pos_id):
                update_position_status(pos_id, "opened")
            else:
                logger.error(f"[MONITOR_PLANNED_ERROR] Nie udało się przenieść pozycji {pos_id} do otwartych w state_manager.")

def monitor_opened_positions(symbol: str):
    opened_positions_for_symbol = list(state_manager.get_all_opened_positions_for_symbol(symbol))
    if not opened_positions_for_symbol: return

    current_price = get_current_price(symbol)
    if current_price is None:
        logger.error(f"[MONITOR_OPENED_ERROR] Brak ceny dla {symbol}, pomijam monitorowanie otwartych.")
        return

    for opened_pos in opened_positions_for_symbol:
        pos_id = opened_pos["position_id"]
        direction = opened_pos["direction"]
        
        try:
            sl_price = float(opened_pos["stop_loss"])
            tp_price = float(opened_pos["take_profit"])
        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"[MONITOR_OPENED_ERROR] Błąd odczytu SL/TP dla pozycji {pos_id}: {e}. Pozycja: {opened_pos}", exc_info=True)
            continue

        closed_reason = None
        actual_close_price = current_price

        if direction == "long":
            if current_price <= sl_price: closed_reason = "SL_HIT"
            elif current_price >= tp_price: closed_reason = "TP_HIT"
        elif direction == "short":
            if current_price >= sl_price: closed_reason = "SL_HIT"
            elif current_price <= tp_price: closed_reason = "TP_HIT"
        
        if closed_reason:
            logger.info(f"[ZAMKNIĘCIE {direction.upper()}] {pos_id} dla {symbol} przez {closed_reason} przy cenie {actual_close_price} (SL: {sl_price}, TP: {tp_price})")
            if state_manager.remove_opened_position(pos_id):
                update_position_status(pos_id, "closed", result_reason=closed_reason)
            else:
                logger.error(f"[MONITOR_OPENED_ERROR] Nie udało się usunąć pozycji {pos_id} z otwartych w state_manager.")