# trading_bot/app/bot_logic.py
import requests
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone

from app import state_manager
from app.positions_logger import log_new_position, update_position_status
from app.constants import (
    BYBIT_API_URL_V5_TICKERS,
    BYBIT_DEFAULT_CATEGORY,
    MAX_ALERT_AGE_SECONDS
)

def get_current_price(symbol: str) -> Optional[float]:
    params = {"category": BYBIT_DEFAULT_CATEGORY, "symbol": symbol}
    try:
        response = requests.get(BYBIT_API_URL_V5_TICKERS, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
            tickers_list = data["result"]["list"]
            if tickers_list:
                return float(tickers_list[0]['lastPrice'])
        else:
            ret_msg = data.get('retMsg', 'Brak wiadomości zwrotnej')
            print(f"[PRICE_ERROR] get_current_price({symbol}): Niepoprawna odpowiedź API Bybit - {ret_msg}")
    except requests.exceptions.HTTPError as http_err:
        print(f"[PRICE_ERROR] HTTP dla {symbol}: {http_err.response.status_code} - {http_err.response.text if http_err.response else 'Brak response'}")
    except requests.exceptions.RequestException as req_err:
        print(f"[PRICE_ERROR] Sieciowy dla {symbol}: {req_err}")
    except (KeyError, IndexError, ValueError) as e:
        print(f"[PRICE_ERROR] Parsowania dla {symbol}: Błąd przetwarzania odpowiedzi API - {e}")
    except Exception as e:
        print(f"[PRICE_ERROR] Ogólny dla {symbol}: {e}")
    return None

def is_alert_recent(alert: dict, max_age_sec: int = MAX_ALERT_AGE_SECONDS) -> bool:
    ts_str = alert.get("timestamp") or alert.get("received_at")
    if not ts_str:
        return False
    try:
        # Normalizuj timestamp do obiektu datetime świadomego strefy czasowej (UTC)
        alert_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if alert_time.tzinfo is None: # Jeśli fromisoformat nie dodał tzinfo
             alert_time = alert_time.replace(tzinfo=timezone.utc)
        
        # Porównaj z aktualnym czasem UTC
        return datetime.now(timezone.utc) - alert_time < timedelta(seconds=max_age_sec)
    except ValueError:
        print(f"[ALERT_TIME_ERROR] Nieprawidłowy format timestampu: {ts_str}")
        return False

def _check_and_plan_position(symbol: str, direction: str):
    current_price = get_current_price(symbol) # Pobierz cenę raz na początku
    # if current_price is None: # Można odkomentować, jeśli cena jest krytyczna już na etapie planowania
    #     print(f"[{direction.upper()}_PLAN] Brak ceny dla {symbol}, nie można planować.")
    #     return

    ob_alerts = state_manager.get_last_orderblocks(symbol)
    if not ob_alerts: return
    
    last_ob = ob_alerts[-1] # Najnowszy OrderBlock
    if last_ob.get("direction", "").lower() != direction: return
    if not is_alert_recent(last_ob):
        # print(f"[{direction.upper()}_PLAN] OrderBlock dla {symbol} nieświeży.") # Loguj rzadziej
        return

    heatmap_event_type = "TOP_GREEN_CHANGE" if direction == "long" else "BOTTOM_RED_CHANGE"
    heatmap_alerts = state_manager.get_last_heatmap(symbol, heatmap_event_type)
    if not heatmap_alerts: return
    
    last_heatmap = heatmap_alerts[-1] # Najnowszy relevantny alert Heatmap
    if not is_alert_recent(last_heatmap):
        # print(f"[{direction.upper()}_PLAN] Heatmap dla {symbol} nieświeży.") # Loguj rzadziej
        return

    try:
        # Użyj pól z JSONa: entry, sl, tp
        ob_entry = float(last_ob["entry"])
        ob_sl = float(last_ob["sl"])
        ob_tp = float(last_ob["tp"]) # Upewnij się, że alert zawiera 'tp'
        heatmap_value = float(last_heatmap["value"])
        
        # Dodatkowe pola z OB do przechowania na potrzeby anulowania
        ob_level_low = float(last_ob.get("levelLow", 0)) # Jeśli nie ma, przyjmij 0 lub inną wartość
        ob_level_high = float(last_ob.get("levelHigh", 0))
        ob_timestamp = last_ob.get("timestamp") # Ważne do identyfikacji OB

    except (KeyError, ValueError) as e:
        print(f"[{direction.upper()}_PLAN_ERROR] Brak kluczowych pól lub błąd konwersji w alertach dla {symbol}: {e}")
        return

    condition_met = False
    if direction == "long": # LONG: SL < Heatmap < Entry
        if ob_sl < heatmap_value < ob_entry:
            condition_met = True
    elif direction == "short": # SHORT: Entry < Heatmap < SL
        if ob_entry < heatmap_value < ob_sl:
            condition_met = True
            
    if condition_met:
        # Sprawdź czy podobna pozycja już nie jest planowana dla tego samego OB
        # (aby uniknąć wielokrotnego planowania tej samej pozycji przez ten sam zestaw alertów)
        for planned_pos_details in state_manager.get_all_planned_positions_for_symbol(symbol):
            if (planned_pos_details.get("triggering_ob_timestamp") == ob_timestamp and
                planned_pos_details.get("direction") == direction):
                # print(f"[{direction.upper()}_PLAN] Pozycja dla {symbol} oparta o ten sam OB ({ob_timestamp}) już zaplanowana.")
                return # Już zaplanowano

        planned_at_iso = datetime.now(timezone.utc).isoformat()
        position_id = state_manager.generate_position_id(symbol, direction, ob_entry, planned_at_iso)
        
        position_details = {
            "position_id": position_id,
            "symbol": symbol,
            "direction": direction,
            "entry_price": ob_entry,
            "stop_loss": ob_sl,
            "take_profit": ob_tp,
            "status": "planned", # Ten status jest w state_manager, logger ma swój
            "planned_at": planned_at_iso,
            "triggering_ob_timestamp": ob_timestamp, # Klucz do identyfikacji OB
            "triggering_ob_level_low": ob_level_low, # Do weryfikacji warunku z opisu
            "triggering_ob_level_high": ob_level_high, # Do weryfikacji warunku z opisu
            "triggering_heatmap_value_at_planning": heatmap_value, # Wartość heatmapy w momencie planowania
        }
        state_manager.add_planned_position(position_id, position_details)
        log_new_position(symbol, direction, ob_entry, ob_sl, ob_tp, position_id)
        print(f"[✅ {direction.upper()}_PLAN] {symbol} | Entry: {ob_entry} | SL: {ob_sl} | TP: {ob_tp} | Heatmap: {heatmap_value}")
    # else:
        # print(f"[⛔ {direction.upper()}_PLAN_FALSE] {symbol} | OB Entry: {ob_entry}, SL: {ob_sl} | Heatmap: {heatmap_value}")


def check_new_long_entries(symbol: str):
    _check_and_plan_position(symbol, "long")

def check_new_short_entries(symbol: str):
    _check_and_plan_position(symbol, "short")


def monitor_planned_positions(symbol: str):
    planned_positions_for_symbol = state_manager.get_all_planned_positions_for_symbol(symbol)
    if not planned_positions_for_symbol: return

    current_price = get_current_price(symbol)
    if current_price is None:
        print(f"[MONITOR_PLANNED_ERROR] Brak ceny dla {symbol}, pomijam monitorowanie planowanych.")
        return

    # Pobierz najnowsze alerty OB i Heatmap dla symbolu
    latest_ob_alerts = state_manager.get_last_orderblocks(symbol)
    latest_ob = latest_ob_alerts[-1] if latest_ob_alerts else None

    for planned_pos in planned_positions_for_symbol: # Iterujemy po kopii lub liście zwróconej przez state_manager
        pos_id = planned_pos["position_id"]
        direction = planned_pos["direction"]
        
        # --- Sprawdzanie warunków anulowania ---
        cancel_reason = None

        # 1. Czy OrderBlock, który wywołał pozycję, wciąż jest "aktualny" lub się nie zmienił drastycznie?
        if latest_ob:
            # Porównujemy timestamp OrderBlocka, który wywołał pozycję, z najnowszym
            if latest_ob.get("timestamp") != planned_pos["triggering_ob_timestamp"]:
                # Dodatkowo można sprawdzić, czy nowy OB ma ten sam kierunek i czy wartości nie są zbyt odległe.
                # Na razie proste: jeśli timestamp się zmienił, OB jest inny.
                # Można też porównać kluczowe wartości jeśli timestamp nie jest unikalny.
                if latest_ob.get("direction", "").lower() == direction: # Nowy OB jest tego samego kierunku
                    # Sprawdźmy, czy entry/sl nie zmieniły się za bardzo (np. o X%)
                    # To bardziej zaawansowane, na razie zmiana timestampu = zmiana OB
                     pass # Można tu dodać logikę, jeśli zmiana timestampu nie zawsze oznacza anulowanie
                
                cancel_reason = f"OrderBlock (ts: {planned_pos['triggering_ob_timestamp']}) zmienił się na nowy (ts: {latest_ob.get('timestamp')})."
        else: # Brak jakiegokolwiek OB
            cancel_reason = "Brak aktualnego OrderBlocka dla symbolu."

        # 2. Czy najnowsza Heatmap wciąż spełnia warunek WEWNĄTRZ pierwotnego OB?
        #    Używamy danych OB z momentu planowania pozycji.
        if not cancel_reason:
            heatmap_event_type = "TOP_GREEN_CHANGE" if direction == "long" else "BOTTOM_RED_CHANGE"
            latest_heatmap_alerts = state_manager.get_last_heatmap(symbol, heatmap_event_type)
            if latest_heatmap_alerts:
                current_heatmap_value = float(latest_heatmap_alerts[-1]["value"])
                
                # Użyj wartości z `planned_pos` do sprawdzenia warunku "wewnątrz OB"
                # np. planned_pos["triggering_ob_level_low"] i planned_pos["triggering_ob_level_high"]
                # lub, jak w logice planowania: planned_pos["stop_loss"] i planned_pos["entry_price"]
                
                entry_at_planning = planned_pos["entry_price"]
                sl_at_planning = planned_pos["stop_loss"]

                if direction == "long": # Oczekiwano SL < Heatmap < Entry
                    if not (sl_at_planning < current_heatmap_value < entry_at_planning):
                        cancel_reason = f"Heatmap TOP_GREEN ({current_heatmap_value}) wyszedł poza zakres pierwotnego OB ({sl_at_planning} - {entry_at_planning})."
                elif direction == "short": # Oczekiwano Entry < Heatmap < SL
                    if not (entry_at_planning < current_heatmap_value < sl_at_planning):
                        cancel_reason = f"Heatmap BOTTOM_RED ({current_heatmap_value}) wyszedł poza zakres pierwotnego OB ({entry_at_planning} - {sl_at_planning})."
            # else: Brak nowego alertu heatmap nie jest powodem do anulowania, chyba że alerty są bardzo stare.

        if cancel_reason:
            print(f"[ANULOWANIE] {pos_id} dla {symbol}: {cancel_reason}")
            state_manager.remove_planned_position(pos_id)
            update_position_status(pos_id, "cancelled", result_reason=cancel_reason)
            continue # Przejdź do następnej zaplanowanej pozycji dla tego symbolu

        # --- Sprawdzanie warunku otwarcia pozycji ---
        entry_price = planned_pos["entry_price"]
        opened = False
        if direction == "long" and current_price >= entry_price:
            opened = True
        elif direction == "short" and current_price <= entry_price:
            opened = True
        
        if opened:
            print(f"[OTWARCIE {direction.upper()}] {pos_id} dla {symbol} przy cenie {current_price} (Entry: {entry_price})")
            state_manager.move_planned_to_opened(pos_id)
            update_position_status(pos_id, "opened")
            # TODO: W przyszłości tutaj wysłanie zlecenia do Bybit


def monitor_opened_positions(symbol: str):
    opened_positions_for_symbol = state_manager.get_all_opened_positions_for_symbol(symbol)
    if not opened_positions_for_symbol: return

    current_price = get_current_price(symbol)
    if current_price is None:
        print(f"[MONITOR_OPENED_ERROR] Brak ceny dla {symbol}, pomijam monitorowanie otwartych.")
        return

    for opened_pos in opened_positions_for_symbol:
        pos_id = opened_pos["position_id"]
        direction = opened_pos["direction"]
        sl_price = opened_pos["stop_loss"]
        tp_price = opened_pos["take_profit"]
        closed_reason = None

        if direction == "long":
            if current_price <= sl_price:
                closed_reason = "SL_HIT"
            elif current_price >= tp_price:
                closed_reason = "TP_HIT"
        elif direction == "short":
            if current_price >= sl_price:
                closed_reason = "SL_HIT"
            elif current_price <= tp_price:
                closed_reason = "TP_HIT"
        
        if closed_reason:
            print(f"[ZAMKNIĘCIE {direction.upper()}] {pos_id} dla {symbol} przez {closed_reason} przy cenie {current_price}")
            state_manager.remove_opened_position(pos_id)
            update_position_status(pos_id, "closed", result_reason=closed_reason)
            # TODO: W przyszłości tutaj zamknięcie zlecenia na Bybit / obsługa SL/TP na giełdzie