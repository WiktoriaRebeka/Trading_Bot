# TRADING_BOT/app/bot_logic.py

import logging
import requests
from typing import Optional, Dict, List, Any
from datetime import datetime, timezone
import time
import hmac
import hashlib

from firebase_admin import firestore
from .firebase_client import get_db
from . import state_manager
from .positions_logger import log_new_position, update_position_status
from .constants import (
    BYBIT_API_URL_V5_TICKERS,
    BYBIT_DEFAULT_CATEGORY,
    BYBIT_API_KEY,
    BYBIT_API_SECRET
)

logger = logging.getLogger(__name__)

# --- POBIERANIE DANYCH ZEWNĘTRZNYCH (BYBIT API) ---

def get_all_prices_for_category(category: str = BYBIT_DEFAULT_CATEGORY) -> Dict[str, float]:
    """Pobiera ceny dla wszystkich symboli, używając kluczy API do autoryzacji."""
    logger.info(f"[GET_PRICES] Rozpoczynam pobieranie cen z autoryzacją dla kategorii: {category}")
    
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        logger.error("[GET_PRICES] Klucze API Bybit nie są skonfigurowane w zmiennych środowiskowych!")
        return {}

    # Bybit wymaga timestampu w milisekundach jako string
    timestamp = str(int(time.time() * 1000))
    recv_window = "10000" # Zwiększone okno dla większej tolerancji na opóźnienia sieciowe
    params = {"category": category}
    query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    
    # Tworzenie sygnatury zgodnie z dokumentacją Bybit V5
    sign_str = timestamp + BYBIT_API_KEY + recv_window + query_string
    signature = hmac.new(bytes(BYBIT_API_SECRET, "utf-8"), bytes(sign_str, "utf-8"), hashlib.sha256).hexdigest()
    
    headers = {
        'X-BAPI-API-KEY': BYBIT_API_KEY,
        'X-BAPI-TIMESTAMP': timestamp,
        'X-BAPI-RECV-WINDOW': recv_window,
        'X-BAPI-SIGN': signature,
        'Content-Type': 'application/json'
    }
    
    all_prices = {}
    try:
        url = f"{BYBIT_API_URL_V5_TICKERS}?{query_string}"
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
             logger.error(f"[GET_PRICES_HTTP_ERROR] Błąd HTTP {response.status_code}. Odpowiedź: {response.text}")
             response.raise_for_status() # Rzuci wyjątkiem dla statusów 4xx/5xx

        data = response.json()
        
        if data.get("retCode") == 0:
            if data.get("result") and data["result"].get("list"):
                for ticker in data["result"]["list"]:
                    symbol = ticker.get("symbol")
                    price_str = ticker.get("lastPrice")
                    if symbol and price_str:
                        try:
                            # Bybit zwraca ceny dla symboli bez .P, np. 'BTCUSDT'
                            all_prices[symbol] = float(price_str)
                        except (ValueError, TypeError):
                            logger.warning(f"[GET_PRICES] Nie udało się sparsować ceny dla {symbol}: '{price_str}'")
                logger.info(f"[GET_PRICES] Pomyślnie pobrano ceny dla {len(all_prices)} symboli.")
            else:
                logger.warning("[GET_PRICES] Odpowiedź API poprawna, ale brak listy tickerów w 'result'.")
            return all_prices
        else:
            logger.error(f"[GET_PRICES_API_ERROR] Błąd API Bybit: Code={data.get('retCode')}, Msg='{data.get('retMsg')}'")
            return {}
            
    except requests.exceptions.RequestException as req_err:
        logger.error(f"[GET_PRICES_REQUEST_ERROR] Błąd połączenia z API Bybit: {req_err}")
    except Exception as e:
        logger.error(f"[GET_PRICES_UNEXPECTED_ERROR] Nieoczekiwany błąd: {e}", exc_info=True)
    return {}

# --- NOWA LOGIKA STRATEGII ---

def process_new_alerts(newly_fetched_alerts: List[Dict]):
    """
    Przetwarza nowe alerty:
    1. Dodaje je do buforów w pamięci (deque).
    2. Jeśli alert to nowy OrderBlock, ustawia go jako aktywny i anuluje stare plany.
    """
    logger.info(f"Rozpoczynam przetwarzanie {len(newly_fetched_alerts)} nowych alertów.")
    # Sortujemy, aby OB były przetwarzane w pierwszej kolejności
    alerts_sorted = sorted(newly_fetched_alerts, key=lambda x: 0 if x.get('type') == 'OrderBlock' else 1)
    
    for alert in alerts_sorted:
        # Krok 1: Zawsze dodajemy alert do bufora `deque`
        state_manager.process_alert(alert)

        # Krok 2: Specjalna logika tylko dla alertów OrderBlock
        if alert.get("type") == "OrderBlock":
            symbol = alert.get("symbol")
            if not symbol: continue
            
            logger.info(f"[{symbol}] Wykryto nowy alert OrderBlock: {alert}")
            
            active_ob_before = state_manager.get_active_order_block(symbol)
            
            # Anuluj zaplanowane pozycje oparte na STARYM OB
            if active_ob_before and active_ob_before.get("timestamp") != alert.get("timestamp"):
                logger.info(f"[{symbol}] Nowy OB unieważnia poprzedni. Sprawdzam, czy są pozycje do anulowania.")
                planned_positions = state_manager.get_all_planned_for_symbol(symbol)
                for pos in planned_positions:
                    if pos.get("triggering_ob_timestamp") == active_ob_before.get("timestamp"):
                        logger.info(f"[{symbol}] Nowy OB unieważnia zaplanowaną pozycję {pos.get('position_id')}. Anulowanie.")
                        db = get_db()
                        transaction = db.transaction()
                        if state_manager.remove_position_transactional(transaction, state_manager.PLANNED_POSITIONS_COLLECTION, pos.get('position_id')):
                            update_position_status(pos.get('position_id'), "cancelled", result_reason="Invalidated by new OB")
            
            # Ustaw nowy OB jako aktywny i zresetuj stan mitigacji
            state_manager.set_active_order_block(symbol, alert)

def run_strategy_cycle(all_active_symbols: List[str], all_prices: Dict[str, float]):
    """
    Główna pętla strategii:
    1. Sprawdza, czy aktywny OB nie został zmitigowany przez cenę.
    2. Jeśli nie, sprawdza warunki do zaplanowania nowej pozycji.
    """
    logger.info(f"--- Uruchamiam cykl strategii dla symboli: {all_active_symbols} ---")
    
    for symbol in all_active_symbols:
        active_ob = state_manager.get_active_order_block(symbol)
        if not active_ob:
            logger.debug(f"[{symbol}] Brak aktywnego OB do analizy. Pomijam.")
            continue

        if state_manager.is_ob_mitigated(symbol):
            logger.debug(f"[{symbol}] Aktywny OB jest już zmitigowany. Czekam na nowy. Pomijam.")
            continue

        # Symbol w API Bybit nie ma ".P"
        current_price = all_prices.get(symbol.replace(".P", ""))
        if current_price is None:
            logger.warning(f"[{symbol}] Brak ceny rynkowej. Pomijam cykl strategii dla tego symbolu.")
            continue

        try:
            direction = active_ob["direction"].lower()
            ob_level_low = float(active_ob["levelLow"])
            ob_level_high = float(active_ob["levelHigh"])
            
            # WARUNEK 1: MITIGACJA PRZEZ CENĘ
            # Sprawdzamy, czy cena weszła w strefę OB
            if ob_level_low <= current_price <= ob_level_high:
                logger.info(f"[{symbol}] Aktywny OB (strefa {ob_level_low}-{ob_level_high}) zmitigowany przez cenę rynkową ({current_price}). Staje się nieaktywny.")
                state_manager.set_ob_as_mitigated(symbol)
                continue # Przechodzimy do następnego symbolu

            # WARUNEK 2: PLANOWANIE NOWEJ POZYCJI (tylko jeśli OB nie jest zmitigowany)
            heatmap_event_type = "TOP_GREEN_CHANGE" if direction == "long" else "BOTTOM_RED_CHANGE"
            heatmap_alerts = state_manager.get_last_heatmap(symbol, heatmap_event_type)
            
            if not heatmap_alerts:
                logger.debug(f"[{symbol}] Brak pasujących alertów heatmapy ({heatmap_event_type}). Pomijam planowanie.")
                continue

            last_heatmap = heatmap_alerts[-1] # Bierzemy najnowszy
            heatmap_value = float(last_heatmap["value"])
            ob_sl_price = float(active_ob["sl"])
            entry_price = float(active_ob["entry"])
            
            is_in_planning_zone = (ob_sl_price < heatmap_value < entry_price) if direction == "long" else (entry_price < heatmap_value < ob_sl_price)
            
            if is_in_planning_zone:
                triggering_ob_timestamp = active_ob["timestamp"]
                position_id = state_manager.generate_position_id(symbol, direction, triggering_ob_timestamp)
                
                # Sprawdź, czy pozycja oparta na tym DOKŁADNYM OB nie jest już zaplanowana
                is_already_planned = any(p.get("position_id") == position_id for p in state_manager.get_all_planned_for_symbol(symbol))

                if not is_already_planned:
                    logger.info(f"[✅ PLAN] {symbol} | {direction.upper()} | Warunki spełnione. Heatmap value ({heatmap_value}) w strefie SL-Entry. Planowanie pozycji.")
                    position_details = {
                        "position_id": position_id, "symbol": symbol, "direction": direction,
                        "entry_price": entry_price, "stop_loss": ob_sl_price, "take_profit": float(active_ob["tp"]),
                        "status": "planned", "planned_at": firestore.SERVER_TIMESTAMP,
                        "triggering_ob_timestamp": triggering_ob_timestamp,
                        "triggering_ob_level_low": ob_level_low, "triggering_ob_level_high": ob_level_high,
                        "triggering_heatmap_value": heatmap_value
                    }
                    state_manager.add_planned_position(position_details)
                    log_new_position(symbol, direction, entry_price, ob_sl_price, float(active_ob["tp"]), position_id, triggering_ob_timestamp)
            else:
                logger.debug(f"[{symbol}] Poziom heatmap ({heatmap_value}) nie spełnia warunku wejścia dla OB: sl={ob_sl_price}, entry={entry_price}.")

        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"[{symbol}] Błąd w cyklu strategii: {e}. Dane OB: {active_ob}", exc_info=True)

def monitor_positions(all_prices: Dict[str, float]):
    """Monitoruje pozycje zaplanowane i otwarte."""
    logger.info("--- Rozpoczynam monitorowanie pozycji (planned/opened) ---")
    all_symbols_with_positions = state_manager.get_all_position_symbols()
    
    if not all_symbols_with_positions:
        logger.info("[MONITOR_POS] Brak pozycji do monitorowania.")
        return
        
    for symbol in all_symbols_with_positions:
        current_price = all_prices.get(symbol.replace(".P", ""))
        if current_price is None:
            logger.warning(f"[{symbol}][MONITOR_POS] Brak aktualnej ceny. Pomijam monitorowanie dla tego symbolu.")
            continue
            
        # Monitorowanie zaplanowanych pozycji
        planned_positions = state_manager.get_all_planned_for_symbol(symbol)
        for pos_details in planned_positions:
            check_and_process_planned_position(pos_details, current_price)
            
        # Monitorowanie otwartych pozycji
        opened_positions = state_manager.get_all_opened_for_symbol(symbol)
        for pos_details in opened_positions:
            check_and_process_opened_position(pos_details, current_price)

def check_and_process_planned_position(planned_pos: Dict[str, Any], current_price: float):
    """Sprawdza warunki anulowania lub otwarcia dla zaplanowanej pozycji."""
    pos_id = planned_pos.get("position_id")
    symbol = planned_pos.get("symbol")
    
    # WARUNEK 1: Anulowanie
    cancel_reason = None
    # Sprawdzamy, czy OB, na którym opiera się plan, został zmitigowany ZANIM cena doszła do entry
    ob_is_mitigated = state_manager.is_ob_mitigated(symbol)
    active_ob = state_manager.get_active_order_block(symbol)
    
    if not active_ob or active_ob.get("timestamp") != planned_pos.get("triggering_ob_timestamp"):
        cancel_reason = "Invalidated by a newer OB"
    elif ob_is_mitigated:
        cancel_reason = "OB mitigated by price before entry"
        
    if cancel_reason:
        logger.info(f"[{symbol}][PLANNED_CANCEL] Anulowanie pozycji {pos_id}. Powód: {cancel_reason}")
        db = get_db()
        transaction = db.transaction()
        if state_manager.remove_position_transactional(transaction, state_manager.PLANNED_POSITIONS_COLLECTION, pos_id):
            update_position_status(pos_id, "cancelled", result_reason=cancel_reason)
        return

    # WARUNEK 2: Otwarcie
    try:
        direction = planned_pos["direction"]
        entry_price = float(planned_pos["entry_price"])
        should_open = (direction == "long" and current_price >= entry_price) or \
                      (direction == "short" and current_price <= entry_price)

        if should_open:
            logger.info(f"[{symbol}][OTWARCIE] Aktywacja pozycji {pos_id} przy cenie {current_price} (entry: {entry_price})")
            db = get_db()
            transaction = db.transaction()
            if state_manager.move_planned_to_opened_transactional(transaction, pos_id):
                update_position_status(pos_id, "opened")

    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[{symbol}][PLANNED_CHECK_ERROR] {pos_id}: Błąd danych: {e}", exc_info=True)

def check_and_process_opened_position(opened_pos: Dict[str, Any], current_price: float):
    """Sprawdza warunki SL/TP dla otwartej pozycji."""
    pos_id = opened_pos.get("position_id")
    symbol = opened_pos.get("symbol")

    try:
        direction = opened_pos["direction"]
        sl_price = float(opened_pos["stop_loss"])
        tp_price = float(opened_pos["take_profit"])
        
        closed_reason = None
        if direction == "long":
            if current_price <= sl_price: closed_reason = "SL_HIT"
            elif current_price >= tp_price: closed_reason = "TP_HIT"
        elif direction == "short":
            if current_price >= sl_price: closed_reason = "SL_HIT"
            elif current_price <= tp_price: closed_reason = "TP_HIT"
        
        if closed_reason:
            logger.info(f"[{symbol}][ZAMKNIĘCIE] Pozycja {pos_id} zamknięta przez {closed_reason} przy cenie {current_price}")
            
            # Oznaczamy OB jako zużyty po zamknięciu pozycji
            state_manager.set_ob_as_mitigated(symbol)

            db = get_db()
            transaction = db.transaction()
            if state_manager.remove_position_transactional(transaction, state_manager.OPENED_POSITIONS_COLLECTION, pos_id):
                update_position_status(pos_id, "closed", result_reason=closed_reason, actual_close_price=current_price)

    except (KeyError, ValueError, TypeError) as e:
        logger.error(f"[{symbol}][OPENED_CHECK_ERROR] {pos_id}: Błąd danych: {e}", exc_info=True)