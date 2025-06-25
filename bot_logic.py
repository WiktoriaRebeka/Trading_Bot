# /trading_bot/bot_logic.py (WERSJA FINALNA v4.1 - Architektura Dwustanowa)

import logging
import requests
import uuid
from typing import Dict, Any, List
from datetime import datetime, timezone

# Importy modułów aplikacji
import state_manager
from bigquery_logger import log_trade_to_bigquery
from constants import BYBIT_API_URL_V5_TICKERS, BYBIT_API_URL_V5_KLINE

logger = logging.getLogger(__name__)

# --- Funkcje pomocnicze (bez zmian) ---

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

def get_all_prices_for_category(category: str = "linear") -> Dict[str, float]:
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


# --- GŁÓWNA LOGIKA BOTA - PRZEBUDOWANA NA DWIE PĘTLE ---

def run_trading_logic(all_prices: Dict[str, float]):
    """Główna pętla logiki, która obsługuje otwieranie i zamykanie pozycji."""
    
    # Krok 1: Zarządzaj otwartymi pozycjami (z kolekcji `active_trades`)
    manage_open_trades(all_prices)
    
    # Krok 2: Zarządzaj nowymi wejściami (z kolekcji `active_setups`)
    manage_new_entries(all_prices)


def manage_open_trades(all_prices: Dict[str, float]):
    """Iteruje po otwartych pozycjach i sprawdza warunki zamknięcia."""
    open_trades = list(state_manager.get_all_trades())
    if not open_trades:
        logger.info("[Zamykanie] Brak otwartych pozycji do monitorowania.")
        return
    
    logger.info(f"[Zamykanie] Monitoruję {len(open_trades)} otwartych pozycji.")
    
    for trade_doc in open_trades:
        trade_id = trade_doc.id
        try:
            trade_data = trade_doc.to_dict()
            symbol = trade_data.get('symbol')
            api_symbol = symbol.replace('.P', '')
            current_price = all_prices.get(api_symbol)

            if current_price is None:
                logger.warning(f"[{symbol}][{trade_id}] Brak ceny dla otwartej pozycji. Pomijam.")
                continue

            direction = trade_data['direction'].lower()
            sl_price = trade_data['sl']
            main_tp_price = trade_data['tp']

            closed_result = None
            if (direction == 'long' and current_price >= main_tp_price) or \
               (direction == 'short' and current_price <= main_tp_price):
                closed_result = "WIN"
            elif (direction == 'long' and current_price <= sl_price) or \
                 (direction == 'short' and current_price >= sl_price):
                closed_result = "LOSE"

            if closed_result:
                logger.info(f"--- [ZAMKNIĘCIE: {closed_result}] --- [{symbol}] | ID: {trade_id} | Cena: {current_price}")
                
                # Pełna analiza KLINE i zapis do BigQuery
                log_closed_trade_to_bigquery(trade_id, trade_data, closed_result)
                
                # Usunięcie pozycji z `active_trades` i setupu z `active_setups`
                state_manager.remove_trade(trade_id)
                state_manager.remove_setup(symbol) # Zamknięcie na SL/TP unieważnia cały setup OB

        except Exception as e:
            logger.error(f"Krytyczny błąd podczas zarządzania otwartą pozycją [{trade_id}]: {e}", exc_info=True)
            state_manager.remove_trade(trade_id) # Bezpieczne usunięcie w razie błędu


def manage_new_entries(all_prices: Dict[str, float]):
    """Iteruje po aktywnych setupach i sprawdza warunki wejścia."""
    active_setups = list(state_manager.get_all_setups())
    if not active_setups:
        logger.info("[Otwieranie] Brak aktywnych setupów do sprawdzenia.")
        return
        
    logger.info(f"[Otwieranie] Sprawdzam {len(active_setups)} aktywnych setupów.")

    open_trade_symbols = state_manager.get_open_trade_symbols()

    for setup_doc in active_setups:
        symbol = setup_doc.id
        try:
            if symbol in open_trade_symbols:
                logger.info(f"[{symbol}] Już jest otwarta pozycja. Pomijam nowe wejście.")
                continue
                
            setup_data = setup_doc.to_dict()
            alert_data = setup_data.get('alert_data', {})
            api_symbol = symbol.replace('.P', '')
            current_price = all_prices.get(api_symbol)

            if current_price is None:
                logger.warning(f"[{symbol}] Brak ceny dla potencjalnego wejścia. Pomijam.")
                continue

            direction = str(alert_data.get('direction', '')).lower()
            entry_level = float(alert_data['entry'])
            last_price = setup_data.get('last_known_price')
            
            should_open = False
            if direction == 'long' and last_price and last_price > entry_level and current_price <= entry_level: should_open = True
            elif direction == 'short' and last_price and last_price < entry_level and current_price >= entry_level: should_open = True

            if should_open:
                is_fresh = setup_data.get('entry_attempts', 0) == 0
                ob_type = "Fresh OB" if is_fresh else "Used OB"
                
                logger.info(f"--- [DECYZJA: WEJŚCIE {ob_type}] --- [{symbol}] | Cena: {current_price}")
                
                # Utwórz nową pozycję w kolekcji `active_trades`
                state_manager.create_trade(symbol, alert_data, current_price, ob_type)
                
                # Zaktualizuj licznik prób wejścia w `active_setups`
                state_manager.increment_entry_attempts(symbol)

            state_manager.update_last_known_price(symbol, current_price)

        except Exception as e:
            logger.error(f"Krytyczny błąd podczas zarządzania nowym wejściem dla [{symbol}]: {e}", exc_info=True)
            state_manager.remove_setup(symbol) # Bezpieczne usunięcie wadliwego setupu


def log_closed_trade_to_bigquery(trade_id: str, trade_data: dict, closed_result: str):
    """Helper do analizy klines i logowania do BigQuery."""
    symbol = trade_data['symbol']
    direction = trade_data['direction'].lower()
    sl_price = trade_data['sl']
    
    close_timestamp_utc = datetime.now(timezone.utc)
    start_time_ms = trade_data['entry_timestamp_ms']
    end_time_ms = int(close_timestamp_utc.timestamp() * 1000)
    
    klines = get_historical_klines(symbol, start_time_ms, end_time_ms)
    
    extreme_profit_price = trade_data.get('entry_price') # Wartość awaryjna
    if klines:
        if direction == 'long': extreme_profit_price = max(float(k[2]) for k in klines)
        else: extreme_profit_price = min(float(k[3]) for k in klines)
    logger.info(f"[{symbol}][{trade_id}] Analiza historyczna. Rzeczywiste ekstremum ceny: {extreme_profit_price}")

    entry_price = trade_data['entry_price']
    risk_price_diff = abs(entry_price - sl_price)
    max_profit_price_diff = abs(extreme_profit_price - entry_price)
    rr_achieved = (max_profit_price_diff / risk_price_diff) if risk_price_diff > 0 else 0.0
    
    achieved_rr_flags = {}
    rr_targets = {k: v for k, v in trade_data.get('alert_data', {}).items() if k.startswith('tp_')}
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

    bq_data = {
        "trade_id": trade_id,
        "timestamp_entry": trade_data['entry_timestamp'],
        "timestamp_close": close_timestamp_utc.isoformat(),
        "symbol": symbol,
        "direction": direction.upper(),
        "main_result": closed_result,
        "ob_type": trade_data.get('ob_type', 'N/A'),
        "rr_achieved": rr_achieved,
        "rr_1_0_achieved": achieved_rr_flags.get("tp_1_0", False),
        "rr_1_5_achieved": achieved_rr_flags.get("tp_1_5", False),
        "rr_2_0_achieved": achieved_rr_flags.get("tp_2_0", False),
        "rr_3_0_achieved": achieved_rr_flags.get("tp_3_0", False),
        "rr_4_0_achieved": achieved_rr_flags.get("tp_4_0", False),
        "rr_5_0_achieved": achieved_rr_flags.get("tp_5_0", False),
    }
    log_trade_to_bigquery(bq_data)
