# /trading_bot/bot_logic.py (WERSJA FINALNA v4.3 z Poprawką)

import logging
import requests
import uuid
from typing import Dict, Any, List
from datetime import datetime, timezone

# Importy modułów aplikacji
import state_manager
from bigquery_logger import log_trade_to_bigquery
import constants

logger = logging.getLogger(__name__)

# --- Funkcje pomocnicze (get_historical_klines, get_all_prices_for_category) pozostają bez zmian ---

def get_historical_klines(symbol: str, start_time_ms: int, end_time_ms: int) -> List[List[Any]]:
    """Pobiera dane historyczne (kline) 1-min z API Bybit."""
    params = {
        "category": "linear", "symbol": symbol.replace('.P', ''), "interval": "1",
        "start": start_time_ms, "end": end_time_ms, "limit": 1000
    }
    logger.info(f"[{symbol}] Pobieram dane kline od {start_time_ms} do {end_time_ms}")
    try:
        response = requests.get(constants.BYBIT_API_URL_V5_KLINE, params=params, timeout=10)
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

def get_all_prices_for_category(category: str = constants.BYBIT_DEFAULT_CATEGORY) -> Dict[str, float]:
    """Pobiera wszystkie ceny tickerów dla danej kategorii z API Bybit."""
    logger.info(f"[GET_PRICES] Rozpoczynam pobieranie cen dla kategorii: {category}")
    params = {"category": category}
    headers = {'User-Agent': 'Mozilla/5.0'}
    all_prices = {}
    try:
        response = requests.get(constants.BYBIT_API_URL_V5_TICKERS, params=params, headers=headers, timeout=10)
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


def _handle_open_new_positions(all_prices: Dict[str, float]):
    """Część logiki odpowiedzialna za otwieranie nowych pozycji na podstawie aktywnych setupów."""
    active_setups = list(state_manager.get_all_active_setups())
    if not active_setups: return
    
    logger.info(f"Sprawdzam {len(active_setups)} aktywnych setupów pod kątem wejścia.")
    
    for setup_doc in active_setups:
        symbol = setup_doc.id
        try:
            setup_data = setup_doc.to_dict()
            alert_data = setup_data.get('alert_data', {})
            
            current_price = all_prices.get(symbol.replace('.P', ''))
            if current_price is None: continue

            # Jeśli dla tego symbolu jest już otwarta pozycja, nie robimy nic więcej.
            # Czekamy, aż zostanie zamknięta.
            if state_manager.is_position_open_for_symbol(symbol):
                continue
            
            # Jeśli pozycja nie jest otwarta, możemy zaktualizować cenę i sprawdzić warunki wejścia.
            last_price = setup_data.get('last_known_price')
            state_manager.update_last_known_price(symbol, current_price) # Bezpieczna aktualizacja

            # Walidacja danych alertu
            try:
                direction = str(alert_data['direction']).lower()
                entry_level = float(alert_data['entry'])
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"[{symbol}] Wadliwy setup, brak kluczowych pól do otwarcia pozycji. Błąd: {e}. Czekam na nowy alert.")
                continue

            should_open = False
            if last_price:
                if direction == 'long' and last_price > entry_level and current_price <= entry_level: should_open = True
                elif direction == 'short' and last_price < entry_level and current_price >= entry_level: should_open = True
            
            if should_open:
                entry_attempts = setup_data.get('entry_attempts', 0)
                ob_type = "Fresh OB" if entry_attempts == 0 else "Used OB"
                trade_id = str(uuid.uuid4())
                
                logger.info(f"--- [DECYZJA: WEJŚCIE {ob_type}] --- [{symbol}] | Cena: {current_price} | ID: {trade_id}")
                
                state_manager.create_open_trade(
                    trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
                    entry_price=current_price, sl_price=float(alert_data['sl']),
                    tp_price=float(alert_data['tp']), alert_data=alert_data
                )

        except Exception as e:
            logger.error(f"[{symbol}] Nieoczekiwany błąd podczas sprawdzania wejścia: {e}", exc_info=True)
            continue


def _handle_manage_open_trades(all_prices: Dict[str, float]):
    """Część logiki odpowiedzialna za monitorowanie i zamykanie otwartych pozycji."""
    open_trades = list(state_manager.get_all_open_trades())
    if not open_trades: return
    
    logger.info(f"Monitoruję {len(open_trades)} otwartych pozycji.")

    for trade_doc in open_trades:
        trade_id = trade_doc.id
        try:
            trade_data = trade_doc.to_dict()
            symbol = trade_data['symbol']
            
            direction = trade_data['direction']
            sl_price = trade_data['sl_price']
            tp_price = trade_data['tp_price']
            
            current_price = all_prices.get(symbol.replace('.P', ''))
            if current_price is None: continue

            closed_result = None
            if direction == 'long' and current_price >= tp_price: closed_result = "WIN"
            elif direction == 'long' and current_price <= sl_price: closed_result = "LOSE"
            elif direction == 'short' and current_price <= tp_price: closed_result = "WIN"
            elif direction == 'short' and current_price >= sl_price: closed_result = "LOSE"
            
            if closed_result:
                log_and_finalize_trade(trade_data, closed_result, current_price)
                state_manager.remove_closed_trade(trade_id)

        except Exception as e:
            logger.error(f"[{trade_id}] Nieoczekiwany błąd podczas monitorowania otwartej pozycji: {e}", exc_info=True)
            continue


def log_and_finalize_trade(trade_data: dict, closed_result: str, close_price: float):
    """Helper do analizy klines i logowania zamkniętej transakcji do BigQuery."""
    trade_id = trade_data['trade_id']
    symbol = trade_data['symbol']
    direction = trade_data['direction']
    
    logger.info(f"--- [ZAMKNIĘCIE: {closed_result}] --- [{symbol}] | ID: {trade_id} | Cena: {close_price}")

    close_timestamp_utc = datetime.now(timezone.utc)
    start_time_ms = trade_data['opened_at_ms']
    
    klines = get_historical_klines(symbol, start_time_ms, int(close_timestamp_utc.timestamp() * 1000))
    
    extreme_profit_price = close_price
    if klines:
        if direction == 'long': extreme_profit_price = max(float(k[2]) for k in klines)
        else: extreme_profit_price = min(float(k[3]) for k in klines)
    logger.info(f"[{symbol}][{trade_id}] Analiza historyczna. Ekstremum ceny: {extreme_profit_price}")

    entry_price = trade_data['entry_price']
    sl_price = trade_data['sl_price']
    
    risk_price_diff = abs(entry_price - sl_price)
    max_profit_price_diff = abs(extreme_profit_price - entry_price)
    rr_achieved = (max_profit_price_diff / risk_price_diff) if risk_price_diff > 0 else 0.0
    
    achieved_rr_flags = {}
    alert_data_snapshot = trade_data.get('alert_data_snapshot', {})
    rr_targets = {k: v for k, v in alert_data_snapshot.items() if k.startswith('tp_')}
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
        "timestamp_entry": trade_data['opened_at_iso'],
        "timestamp_close": close_timestamp_utc.isoformat(),
        "symbol": symbol,
        "direction": direction.upper(),
        "main_result": closed_result,
        "ob_type": trade_data['ob_type'],
        "rr_achieved": rr_achieved,
        "rr_1_0_achieved": achieved_rr_flags.get("tp_1_0", False),
        "rr_1_5_achieved": achieved_rr_flags.get("tp_1_5", False),
        "rr_2_0_achieved": achieved_rr_flags.get("tp_2_0", False),
        "rr_3_0_achieved": achieved_rr_flags.get("tp_3_0", False),
        "rr_4_0_achieved": achieved_rr_flags.get("tp_4_0", False),
        "rr_5_0_achieved": achieved_rr_flags.get("tp_5_0", False),
    }
    log_trade_to_bigquery(bq_data)


def run_trading_logic(all_prices: Dict[str, float]):
    """Główna funkcja orkiestrująca, wywoływana z main.py."""
    
    try:
        # Krok 1: Sprawdź, czy można otworzyć jakieś nowe pozycje
        _handle_open_new_positions(all_prices)
    except Exception as e:
        logger.error(f"Nieoczekiwany błąd w _handle_open_new_positions: {e}", exc_info=True)

    try:
        # Krok 2: Zarządzaj wszystkimi już otwartymi pozycjami
        _handle_manage_open_trades(all_prices)
    except Exception as e:
        logger.error(f"Nieoczekiwany błąd w _handle_manage_open_trades: {e}", exc_info=True)