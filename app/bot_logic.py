# trading_bot/app/bot_logic.py
import requests
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import traceback # Dodany import dla pełnego tracebacku

from app import state_manager
from app.positions_logger import log_new_position, update_position_status
from app.constants import (
    BYBIT_API_URL_V5_TICKERS,
    BYBIT_DEFAULT_CATEGORY,
    MAX_ALERT_AGE_SECONDS
)


def get_bybit_compatible_symbol(tv_symbol: str) -> str:
    """Konwertuje symbol z TradingView na format akceptowany przez Bybit API V5."""
    if tv_symbol.endswith(".P"):
        return tv_symbol[:-2] # Usuń ostatnie dwa znaki (".P")
    # Możesz dodać tu inne reguły, jeśli masz inne formaty symboli z TV
    return tv_symbol

def get_current_price(raw_symbol: str) -> Optional[float]:
    cleaned_symbol = get_bybit_compatible_symbol(raw_symbol)
    params = {"category": BYBIT_DEFAULT_CATEGORY, "symbol": cleaned_symbol}
    headers = {
        'User-Agent': 'TradingBot/1.0 (Python Requests; AppEngine; +https://ваш-проект-url-lub-kontakt)', # ZMIEŃ NA SWÓJ KONTAKT/URL PROJEKTU
        'Accept': 'application/json'
    }
    
    # Pełny URL dla logowania (opcjonalnie, ale pomaga w debugowaniu)
    # query_string = requests.compat.urlencode(params)
    # full_url_for_debug = f"{BYBIT_API_URL_V5_TICKERS}?{query_string}"
    # print(f"[PRICE_DEBUG] Próba pobrania ceny dla {cleaned_symbol} z URL: {full_url_for_debug} z headers: {headers}")
    # Lub prościej:
    print(f"[PRICE_DEBUG] Próba pobrania ceny dla {cleaned_symbol} z URL: {BYBIT_API_URL_V5_TICKERS} z params: {params} i headers: {headers}")

    try:
        response = requests.get(BYBIT_API_URL_V5_TICKERS, params=params, headers=headers, timeout=10) # Zwiększony timeout
        
        # Logowanie statusu i nagłówków odpowiedzi ZAWSZE, niezależnie od sukcesu
        print(f"[PRICE_DEBUG] Odpowiedź dla {cleaned_symbol} - Status: {response.status_code}, Nagłówki odpowiedzi (fragment): {str(response.headers)[:200]}...") # Loguj fragment nagłówków

        # Opcjonalne logowanie surowej treści odpowiedzi PRZED próbą parsowania JSON i raise_for_status
        # Może być bardzo pomocne, jeśli serwer zwraca błąd, ale nie jest to standardowy HTTPError
        # print(f"[PRICE_DEBUG_RAW_CONTENT] Surowa treść odpowiedzi dla {cleaned_symbol} (pierwsze 500 znaków): {response.text[:500]}")

        response.raise_for_status() # Rzuci wyjątek dla błędów 4xx/5xx
        data = response.json()

        if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
            tickers_list = data["result"]["list"]
            if tickers_list:
                price_str = tickers_list[0].get('lastPrice')
                if price_str is not None:
                    price = float(price_str)
                    print(f"[PRICE_INFO] Pobrana cena dla {cleaned_symbol}: {price}")
                    return price
                else:
                    print(f"[PRICE_ERROR] get_current_price({cleaned_symbol}): Brak 'lastPrice' w odpowiedzi. Odpowiedź API: {data}")
                    return None
            else:
                print(f"[PRICE_ERROR] get_current_price({cleaned_symbol}): Lista tickerów pusta. Odpowiedź API: {data}")
                return None
        else:
            ret_msg = data.get('retMsg', 'Brak wiadomości zwrotnej')
            ret_code = data.get('retCode', 'Brak kodu zwrotnego')
            print(f"[PRICE_ERROR] get_current_price({cleaned_symbol}): Niepoprawna odpowiedź API Bybit - Kod: {ret_code}, Wiadomość: '{ret_msg}'. Pełna odpowiedź: {data}")
            return None

    except requests.exceptions.HTTPError as http_err:
        response_text_snippet = "Brak response.text"
        response_headers_str = "Brak response.headers"
        status_code_val = "Brak response.status_code"
        req_url_str = "Brak request.url"
        req_headers_str = "Brak request.headers"

        if http_err.response is not None:
            response_text_snippet = http_err.response.text[:500] # Fragment tekstu
            response_headers_str = str(http_err.response.headers)
            status_code_val = str(http_err.response.status_code) # Upewnij się, że jest stringiem dla print
            print(f"[PRICE_ERROR_HTTP_RAW_CONTENT] Surowa treść odpowiedzi błędu dla {cleaned_symbol} (pierwsze 500 znaków): {http_err.response.text[:500]}")

        if hasattr(http_err, 'request') and http_err.request is not None:
            req_url_str = str(http_err.request.url)
            req_headers_str = str(http_err.request.headers)
        else:
            # Fallback: spróbuj zrekonstruować URL, jeśli obiekt request nie jest dostępny
            query_string_fallback = requests.compat.urlencode(params)
            req_url_str = f"{BYBIT_API_URL_V5_TICKERS}?{query_string_fallback}"
            req_headers_str = str(headers) # Użyj nagłówków, które próbowaliśmy wysłać

        print(f"[PRICE_ERROR] HTTP dla {cleaned_symbol}: Status '{status_code_val}'")
        print(f"  URL żądania: {req_url_str}")
        print(f"  Nagłówki żądania: {req_headers_str}")
        print(f"  Nagłówki odpowiedzi (fragment): {response_headers_str[:200]}...")
        print(f"  Tekst odpowiedzi (fragment): '{response_text_snippet}'")
        print(f"  Pełny obiekt błędu: {http_err}") # Loguje reprezentację stringową całego obiektu błędu
        return None
    
    except requests.exceptions.Timeout:
        print(f"[PRICE_ERROR] Timeout (po {10}s) podczas połączenia z Bybit dla {cleaned_symbol} (URL: {BYBIT_API_URL_V5_TICKERS}, Params: {params})")
        return None
    except requests.exceptions.ConnectionError as conn_err:
        print(f"[PRICE_ERROR] ConnectionError dla {cleaned_symbol}: {conn_err} (URL: {BYBIT_API_URL_V5_TICKERS}, Params: {params})")
        return None
    except requests.exceptions.RequestException as req_err: # Ogólny błąd z biblioteki requests
        print(f"[PRICE_ERROR] Inny RequestException dla {cleaned_symbol}: {req_err} (URL: {BYBIT_API_URL_V5_TICKERS}, Params: {params})")
        return None
    except (KeyError, IndexError, ValueError, TypeError) as e: # Dodano TypeError, np. dla float(None)
        print(f"[PRICE_ERROR] Parsowania/Przetwarzania dla {cleaned_symbol}: Błąd przetwarzania odpowiedzi API - {type(e).__name__}: {e}.")
        print(f"  Sprawdź logi [PRICE_DEBUG] i [PRICE_DEBUG_RAW_CONTENT] dla surowej odpowiedzi, jeśli była.")
        return None
    except Exception as e:
        print(f"[PRICE_ERROR] Ogólny nieoczekiwany błąd w get_current_price dla {cleaned_symbol}: {type(e).__name__}: {e}")
        traceback.print_exc() # Wydrukuje pełny traceback do logów App Engine
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
    # Pobierz cenę na początku, ale tylko jeśli jest potrzebna do planowania
    # W tej logice cena nie jest potrzebna do samego planowania, więc można ją pominąć tutaj
    # i pobierać tylko w monitor_planned_positions.
    # current_price = get_current_price(symbol)
    # if current_price is None:
    #     print(f"[{direction.upper()}_PLAN_WARN] Brak ceny dla {symbol}, ale kontynuuję planowanie jeśli możliwe.")
        # return # Można odkomentować, jeśli cena jest krytyczna już na etapie planowania

    ob_alerts = state_manager.get_last_orderblocks(symbol)
    if not ob_alerts: return
    
    last_ob = ob_alerts[-1] # Najnowszy OrderBlock
    if last_ob.get("direction", "").lower() != direction.lower(): return # Użyj .lower() dla pewności
    if not is_alert_recent(last_ob):
        # print(f"[{direction.upper()}_PLAN_DEBUG] OrderBlock dla {symbol} nieświeży.") # Loguj rzadziej
        return

    heatmap_event_type = "TOP_GREEN_CHANGE" if direction.lower() == "long" else "BOTTOM_RED_CHANGE"
    heatmap_alerts = state_manager.get_last_heatmap(symbol, heatmap_event_type)
    if not heatmap_alerts: return
    
    last_heatmap = heatmap_alerts[-1] # Najnowszy relevantny alert Heatmap
    if not is_alert_recent(last_heatmap):
        # print(f"[{direction.upper()}_PLAN_DEBUG] Heatmap dla {symbol} nieświeży.") # Loguj rzadziej
        return

    try:
        # Użyj pól z JSONa: entry, sl, tp
        ob_entry = float(last_ob["entry"])
        ob_sl = float(last_ob["sl"])
        ob_tp = float(last_ob["tp"]) # Upewnij się, że alert zawiera 'tp'
        heatmap_value = float(last_heatmap["value"])
        
        ob_level_low = float(last_ob.get("levelLow", 0))
        ob_level_high = float(last_ob.get("levelHigh", 0))
        ob_timestamp = last_ob.get("timestamp")

    except (KeyError, ValueError, TypeError) as e: # Dodano TypeError
        print(f"[{direction.upper()}_PLAN_ERROR] Brak kluczowych pól lub błąd konwersji w alertach dla {symbol}: {type(e).__name__}: {e}. Alerty: OB={last_ob}, Heatmap={last_heatmap}")
        return

    condition_met = False
    if direction.lower() == "long": # LONG: SL < Heatmap < Entry
        if ob_sl < heatmap_value < ob_entry:
            condition_met = True
    elif direction.lower() == "short": # SHORT: Entry < Heatmap < SL
        if ob_entry < heatmap_value < ob_sl:
            condition_met = True
            
    if condition_met:
        for planned_pos_details in state_manager.get_all_planned_positions_for_symbol(symbol):
            if (planned_pos_details.get("triggering_ob_timestamp") == ob_timestamp and
                planned_pos_details.get("direction") == direction.lower()):
                # print(f"[{direction.upper()}_PLAN_DEBUG] Pozycja dla {symbol} oparta o ten sam OB ({ob_timestamp}) już zaplanowana.")
                return

        planned_at_iso = datetime.now(timezone.utc).isoformat()
        position_id = state_manager.generate_position_id(symbol, direction.lower(), ob_entry, planned_at_iso)
        
        position_details = {
            "position_id": position_id,
            "symbol": symbol,
            "direction": direction.lower(),
            "entry_price": ob_entry,
            "stop_loss": ob_sl,
            "take_profit": ob_tp,
            "status": "planned",
            "planned_at": planned_at_iso,
            "triggering_ob_timestamp": ob_timestamp,
            "triggering_ob_level_low": ob_level_low,
            "triggering_ob_level_high": ob_level_high,
            "triggering_heatmap_value_at_planning": heatmap_value,
        }
        state_manager.add_planned_position(position_id, position_details)
        log_new_position(symbol, direction.lower(), ob_entry, ob_sl, ob_tp, position_id)
        print(f"[✅ {direction.upper()}_PLAN] {symbol} | Entry: {ob_entry} | SL: {ob_sl} | TP: {ob_tp} | Heatmap: {heatmap_value}")
    # else:
        # print(f"[⛔ {direction.upper()}_PLAN_COND_FALSE] {symbol} | OB Entry: {ob_entry}, SL: {ob_sl} | Heatmap: {heatmap_value} | Direction: {direction}")


def check_new_long_entries(symbol: str):
    _check_and_plan_position(symbol, "long")

def check_new_short_entries(symbol: str):
    _check_and_plan_position(symbol, "short")


def monitor_planned_positions(symbol: str):
    # Należy pobrać kopię listy, aby uniknąć modyfikacji podczas iteracji, jeśli state_manager zwraca referencję
    planned_positions_for_symbol = list(state_manager.get_all_planned_positions_for_symbol(symbol))
    if not planned_positions_for_symbol: return

    current_price = get_current_price(symbol) # Pobierz cenę raz dla wszystkich planowanych pozycji dla danego symbolu
    if current_price is None:
        print(f"[MONITOR_PLANNED_ERROR] Brak ceny dla {symbol}, pomijam monitorowanie planowanych.")
        return

    latest_ob_alerts = state_manager.get_last_orderblocks(symbol)
    latest_ob = latest_ob_alerts[-1] if latest_ob_alerts else None

    for planned_pos in planned_positions_for_symbol:
        pos_id = planned_pos["position_id"]
        direction = planned_pos["direction"] # Powinien być już lower-case z _check_and_plan_position
        
        cancel_reason = None

        if latest_ob:
            if latest_ob.get("timestamp") != planned_pos.get("triggering_ob_timestamp"):
                # Jeśli OB się zmienił, to może być powód do anulowania
                # Można dodać bardziej złożoną logikę, np. jeśli nowy OB ma inny kierunek itp.
                cancel_reason = f"OrderBlock (ts: {planned_pos.get('triggering_ob_timestamp')}) zmienił się na nowy (ts: {latest_ob.get('timestamp')})."
        else:
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

                        if direction == "long":
                            if not (sl_at_planning < current_heatmap_value < entry_at_planning):
                                cancel_reason = f"Heatmap TOP_GREEN ({current_heatmap_value}) wyszedł poza zakres pierwotnego OB ({sl_at_planning} - {entry_at_planning})."
                        elif direction == "short":
                            if not (entry_at_planning < current_heatmap_value < sl_at_planning):
                                cancel_reason = f"Heatmap BOTTOM_RED ({current_heatmap_value}) wyszedł poza zakres pierwotnego OB ({entry_at_planning} - {sl_at_planning})."
                    except (ValueError, TypeError) as e:
                        print(f"[MONITOR_PLANNED_ERROR] Błąd konwersji wartości dla {pos_id}: {e}")
                        cancel_reason = f"Błąd danych heatmapy lub pozycji ({e})."
                else:
                    print(f"[MONITOR_PLANNED_WARN] Brak wartości 'value' w ostatnim alercie heatmap dla {pos_id}")
                    # Można uznać za powód do anulowania lub zignorować, w zależności od strategii
            # else: Brak nowego alertu heatmap nie musi być powodem do anulowania, chyba że alerty są bardzo stare.

        if cancel_reason:
            print(f"[ANULOWANIE] {pos_id} dla {symbol}: {cancel_reason}")
            state_manager.remove_planned_position(pos_id) # Usuwa ze state_manager
            update_position_status(pos_id, "cancelled", result_reason=cancel_reason) # Aktualizuje w Firestore
            continue 

        # --- Sprawdzanie warunku otwarcia pozycji ---
        # Użyj `current_price` pobranego na początku funkcji
        entry_price_planned = float(planned_pos["entry_price"])
        opened = False
        if direction == "long" and current_price >= entry_price_planned:
            opened = True
        elif direction == "short" and current_price <= entry_price_planned:
            opened = True
        
        if opened:
            print(f"[OTWARCIE {direction.upper()}] {pos_id} dla {symbol} przy cenie {current_price} (Planowane Entry: {entry_price_planned})")
            # Aktualizacja statusu w state_manager i Firestore
            if state_manager.move_planned_to_opened(pos_id): # move_planned_to_opened powinno zwracać True jeśli się udało
                update_position_status(pos_id, "opened") # Aktualizuje w Firestore
                # TODO: W przyszłości tutaj wysłanie zlecenia do Bybit
            else:
                print(f"[MONITOR_PLANNED_ERROR] Nie udało się przenieść pozycji {pos_id} do otwartych w state_manager.")


def monitor_opened_positions(symbol: str):
    opened_positions_for_symbol = list(state_manager.get_all_opened_positions_for_symbol(symbol))
    if not opened_positions_for_symbol: return

    current_price = get_current_price(symbol)
    if current_price is None:
        print(f"[MONITOR_OPENED_ERROR] Brak ceny dla {symbol}, pomijam monitorowanie otwartych.")
        return

    for opened_pos in opened_positions_for_symbol:
        pos_id = opened_pos["position_id"]
        direction = opened_pos["direction"]
        
        try:
            sl_price = float(opened_pos["stop_loss"])
            tp_price = float(opened_pos["take_profit"])
        except (ValueError, TypeError, KeyError) as e:
            print(f"[MONITOR_OPENED_ERROR] Błąd odczytu SL/TP dla pozycji {pos_id}: {e}. Pozycja: {opened_pos}")
            continue # Pomiń tę pozycję, jeśli dane są niepoprawne

        closed_reason = None
        actual_close_price = current_price # Domyślnie, jeśli SL/TP jest trafiony

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
            print(f"[ZAMKNIĘCIE {direction.upper()}] {pos_id} dla {symbol} przez {closed_reason} przy cenie {actual_close_price} (SL: {sl_price}, TP: {tp_price})")
            if state_manager.remove_opened_position(pos_id): # Usuwa ze state_manager
                update_position_status(pos_id, "closed", result_reason=closed_reason) # Aktualizuje w Firestore
                # TODO: W przyszłości tutaj zamknięcie zlecenia na Bybit / obsługa SL/TP na giełdzie
            else:
                print(f"[MONITOR_OPENED_ERROR] Nie udało się usunąć pozycji {pos_id} z otwartych w state_manager.")