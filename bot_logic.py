# /trading_bot/bot_logic.py (Wersja Ostateczna dla Planu v5.2)

import logging
import requests
import uuid
from typing import Dict, Any, List
from datetime import datetime, timezone

# Importy modułów aplikacji
import state_manager
from bigquery_logger import log_trade_to_bigquery
from constants import BYBIT_API_URL_V5_TICKERS, BYBIT_API_URL_V5_KLINE, BYBIT_DEFAULT_CATEGORY

logger = logging.getLogger(__name__)

def get_historical_klines(symbol: str, start_time_ms: int, end_time_ms: int) -> List[List[Any]]:
    """Pobiera dane historyczne (kline) 1-min z API Bybit."""
    params = {
        "category": "linear",
        "symbol": symbol.replace('.P', ''),
        "interval": "1",
        "start": start_time_ms,
        "end": end_time_ms,
        "limit": 1000
    }
    logger.info(f"[{symbol}] Pobieram dane kline od {start_time_ms} do {end_time_ms}")
    try:
        response = requests.get(BYBIT_API_URL_V5_KLINE, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
            klines = data["result"]["list"]
            logger.info(f"[{symbol}] Pomyślnie pobrano {len(klines)} świec historycznych.")
            return list(reversed(klines))
        else:
            logger.error(f"[{symbol}] Błąd API Bybit (kline): {data.get('retMsg')}")
            return []
    except Exception as e:
        logger.error(f"[{symbol}] Nieoczekiwany błąd przy pobieraniu kline: {e}", exc_info=True)
        return []

def get_all_prices_for_category(category: str = BYBIT_DEFAULT_CATEGORY) -> Dict[str, float]:
    """Pobiera wszystkie ceny tickerów dla danej kategorii z API Bybit."""
    logger.info(f"[GET_PRICES] Rozpoczynam pobieranie cen dla kategorii: {category}")
    params = {"category": category}
    headers = {'User-Agent': 'Mozilla/5.0'}
    all_prices = {}
    try:
        response = requests.get(BYBIT_API_URL_V5_TICKERS, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
            for ticker in data["result"]["list"]:
                symbol, price_str = ticker.get("symbol"), ticker.get("lastPrice")
                if symbol and price_str:
                    try: all_prices[symbol] = float(price_str)
                    except ValueError: pass
            logger.info(f"[GET_PRICES] Pomyślnie pobrano ceny dla {len(all_prices)} symboli.")
        else:
            logger.error(f"[GET_PRICES] Błąd API Bybit: {data.get('retMsg')}")
    except Exception as e:
        logger.error(f"[GET_PRICES] Nieoczekiwany błąd: {e}", exc_info=True)
    return all_prices

def run_trading_logic(all_prices: Dict[str, float]):
    """Główna pętla logiki oparta na architekturze stanu ciągłego."""
    active_symbols_stream = state_manager.get_all_active_symbols()
    active_symbols = [doc.id for doc in active_symbols_stream]

    if not active_symbols:
        logger.info("Brak aktywnych setupów do monitorowania.")
        return

    logger.info(f"Monitoruję setupy dla symboli: {active_symbols}")

    for symbol in active_symbols:
        try:
            setup = state_manager.get_active_setup(symbol)
            if not setup: continue

            api_symbol = symbol.replace('.P', '')
            current_price = all_prices.get(api_symbol)
            if current_price is None:
                logger.warning(f"[{symbol}] Brak aktualnej ceny. Pomijam cykl dla tego symbolu.")
                continue

            alert_data = setup.get('alert_data')
            if not isinstance(alert_data, dict):
                logger.error(f"[{symbol}] Uszkodzony alert_data w setupie. Usuwam.")
                state_manager.remove_setup(symbol)
                continue

            try:
                direction = str(alert_data['direction']).lower()
                entry_level = float(alert_data['entry'])
                sl_price = float(alert_data['sl'])
                main_tp_price = float(alert_data['tp'])
            except (KeyError, ValueError, TypeError) as e:
                logger.error(f"[{symbol}] Wadliwy alert (brak pól lub zły typ). Błąd: {e}. Usuwam setup.")
                state_manager.remove_setup(symbol)
                continue

            is_position_open = setup.get('is_position_open', False)

            if is_position_open:
                # --- LOGIKA ZARZĄDZANIA OTWARTĄ POZYCJĄ ---
                closed_result = None
                if (direction == 'long' and current_price >= main_tp_price) or \
                   (direction == 'short' and current_price <= main_tp_price):
                    closed_result = "WIN"
                elif (direction == 'long' and current_price <= sl_price) or \
                     (direction == 'short' and current_price >= sl_price):
                    closed_result = "LOSE"
                
                if closed_result:
                    log_and_finalize_trade(setup, symbol, closed_result, current_price)
                    # Zamknięcie na główny TP/SL unieważnia cały OB i usuwa setup.
                    state_manager.remove_setup(symbol)
            
            else: # Pozycja nie jest otwarta
                # --- LOGIKA OTWIERANIA NOWEJ POZYCJI ---
                last_price = setup.get('last_known_price')
                entry_attempts = setup.get('entry_attempts', 0)
                
                should_open = False
                # Warunek wejścia jest ten sam dla Fresh i Used, ale dla Used wymaga resetu.
                entry_condition_met = False
                if last_price:
                    # Dla LONG, cena musi przyjść z dołu (last < entry) i przebić w górę (current >= entry)
                    if direction == 'long' and last_price < entry_level and current_price >= entry_level: entry_condition_met = True
                    # Dla SHORT, cena musi przyjść z góry (last > entry) i przebić w dół (current <= entry)
                    elif direction == 'short' and last_price > entry_level and current_price <= entry_level: entry_condition_met = True
                
                if entry_attempts == 0: # Logika dla Fresh OB
                    if entry_condition_met:
                        should_open = True
                else: # Logika dla Used OB
                    is_reset_for_reentry = False
                    # Dla LONG, reset następuje, gdy cena jest PONIŻEJ wejścia
                    if direction == 'long' and current_price < entry_level: is_reset_for_reentry = True
                    # Dla SHORT, reset następuje, gdy cena jest POWYŻEJ wejścia
                    elif direction == 'short' and current_price > entry_level: is_reset_for_reentry = True

                    if is_reset_for_reentry and entry_condition_met:
                        should_open = True

                if should_open:
                    ob_type = "Fresh OB" if entry_attempts == 0 else "Used OB"
                    trade_id = str(uuid.uuid4())
                    logger.info(f"--- [DECYZJA: WEJŚCIE {ob_type}] --- [{symbol}] | Cena: {current_price} | ID: {trade_id}")
                    state_manager.open_new_position(symbol, trade_id, current_price, datetime.now(timezone.utc))

            # Zawsze aktualizuj ostatnią cenę, jeśli setup nadal istnieje
            if state_manager.get_active_setup(symbol):
                state_manager.update_last_known_price(symbol, current_price)

        except Exception as e:
            logger.error(f"KRYTYCZNY BŁĄD w pętli dla symbolu [{symbol}]. Usuwam setup. Błąd: {e}", exc_info=True)
            state_manager.remove_setup(symbol)
            continue

def log_and_finalize_trade(setup: dict, symbol: str, closed_result: str, close_price: float):
    """Helper do analizy klines i logowania zamkniętej transakcji do BigQuery."""
    trade_id = setup['active_trade_id']
    logger.info(f"--- [ZAMKNIĘCIE: {closed_result}] --- [{symbol}] | ID: {trade_id} | Cena: {close_price}")

    alert_data = setup['alert_data']
    direction = str(alert_data['direction']).lower()
    sl_price = float(alert_data['sl'])
    
    close_timestamp_utc = datetime.now(timezone.utc)
    start_time_ms = setup['active_trade_entry_timestamp_ms']
    end_time_ms = int(close_timestamp_utc.timestamp() * 1000)
    
    klines = get_historical_klines(symbol, start_time_ms, end_time_ms)
    
    extreme_profit_price = close_price
    if klines:
        if direction == 'long': extreme_profit_price = max(float(k[2]) for k in klines)
        else: extreme_profit_price = min(float(k[3]) for k in klines)
    logger.info(f"[{symbol}][{trade_id}] Analiza historyczna. Ekstremum ceny: {extreme_profit_price}")

    entry_price = setup['active_trade_entry_price']
    risk_price_diff = abs(entry_price - sl_price)
    max_profit_price_diff = abs(extreme_profit_price - entry_price)
    rr_achieved = (max_profit_price_diff / risk_price_diff) if risk_price_diff > 0 else 0.0
    
    achieved_rr_flags = {}
    rr_targets = {k: v for k, v in alert_data.items() if k.startswith('tp_')}
    for rr_key, tp_value in rr_targets.items():
        if tp_value is not None:
            try:
                tp_price = float(tp_value)
                if (direction == 'long' and extreme_profit_price >= tp_price) or \
                   (direction == 'short' and extreme_profit_price <= tp_price):
                    achieved_rr_flags[rr_key] = True
                else:
                    achieved_rr_flags[rr_key] = False
            except (ValueError, TypeError):
                achieved_rr_flags[rr_key] = False

    ob_type = "Fresh OB" if setup.get('entry_attempts', 1) <= 1 else "Used OB"

    bq_data = {
        "trade_id": trade_id,
        "timestamp_entry": datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc).isoformat(),
        "timestamp_close": close_timestamp_utc.isoformat(),
        "symbol": symbol,
        "direction": direction.upper(),
        "main_result": closed_result,
        "ob_type": ob_type,
        "rr_achieved": rr_achieved,
        "rr_1_0_achieved": achieved_rr_flags.get("tp_1_0", False),
        "rr_1_5_achieved": achieved_rr_flags.get("tp_1_5", False),
        "rr_2_0_achieved": achieved_rr_flags.get("tp_2_0", False),
        "rr_3_0_achieved": achieved_rr_flags.get("tp_3_0", False),
        "rr_4_0_achieved": achieved_rr_flags.get("tp_4_0", False),
        "rr_5_0_achieved": achieved_rr_flags.get("tp_5_0", False),
    }
    log_trade_to_bigquery(bq_data)
