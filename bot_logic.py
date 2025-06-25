# /trading_bot/bot_logic.py (WERSJA OSTATECZNA - Zintegrowana z Planem Naprawczym v2.1)

import logging
import requests
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
    """Główna pętla logiki, która monitoruje wszystkie aktywne transakcje."""
    active_trades_stream = state_manager.get_all_active_trades()
    active_trades = list(active_trades_stream)

    if not active_trades:
        logger.info("Brak aktywnych transakcji do monitorowania.")
        return

    logger.info(f"Monitoruję {len(active_trades)} aktywnych transakcji/setupów.")

    for trade_doc in active_trades:
        trade_id = trade_doc.id
        try:
            trade_state = trade_doc.to_dict()
            symbol = trade_state.get('symbol')
            if not symbol:
                logger.error(f"Brak pola 'symbol' w dokumencie transakcji {trade_id}. Usuwam.")
                state_manager.remove_trade(trade_id)
                continue

            api_symbol = symbol.replace('.P', '')
            current_price = all_prices.get(api_symbol)
            if current_price is None:
                logger.warning(f"[{symbol}][{trade_id}] Brak aktualnej ceny dla symbolu. Pomijam cykl.")
                continue

            status = trade_state.get('status')
            alert_data = trade_state.get('alert_data')
            
            if not isinstance(alert_data, dict):
                logger.error(f"[{symbol}][{trade_id}] Pole 'alert_data' jest uszkodzone lub go brakuje. Usuwam stan. Stan: {trade_state}")
                state_manager.remove_trade(trade_id)
                continue
            
            if status == 'PENDING_ENTRY':
                try:
                    direction = str(alert_data['direction']).lower()
                    entry_level = float(alert_data['entry'])
                except (KeyError, ValueError, TypeError) as e:
                    logger.error(f"[{symbol}][{trade_id}] Wadliwy alert dla PENDING_ENTRY (brak 'direction'/'entry' lub zły typ). Błąd: {e}. Usuwam. Alert: {alert_data}")
                    state_manager.remove_trade(trade_id)
                    continue

                last_price = trade_state.get('last_known_price')
                
                should_open = False
                if direction == 'long' and last_price and last_price > entry_level and current_price <= entry_level: should_open = True
                elif direction == 'short' and last_price and last_price < entry_level and current_price >= entry_level: should_open = True

                if should_open:
                    logger.info(f"--- [DECYZJA: WEJŚCIE] --- [{symbol}] | Cena: {current_price} | ID: {trade_id}")
                    state_manager.update_trade_status_to_open(trade_id, current_price, datetime.now(timezone.utc))
                
                state_manager.update_last_known_price(trade_id, current_price)
                
            elif status == 'OPEN':
                try:
                    direction = str(alert_data['direction']).lower()
                    sl_price = float(alert_data['sl'])
                    main_tp_price = float(alert_data['tp'])
                except (KeyError, ValueError, TypeError) as e:
                    logger.error(f"[{symbol}][{trade_id}] Wadliwy alert dla OPEN (brak 'direction'/'sl'/'tp' lub zły typ). Błąd: {e}. Usuwam. Alert: {alert_data}")
                    state_manager.remove_trade(trade_id)
                    continue

                closed_result = None
                if (direction == 'long' and current_price >= main_tp_price) or \
                   (direction == 'short' and current_price <= main_tp_price):
                    closed_result = "WIN"
                elif (direction == 'long' and current_price <= sl_price) or \
                     (direction == 'short' and current_price >= sl_price):
                    closed_result = "LOSE"
                
                if closed_result:
                    logger.info(f"--- [ZAMKNIĘCIE: {closed_result}] --- [{symbol}] | ID: {trade_id} | Cena: {current_price}")
                    
                    close_timestamp_utc = datetime.now(timezone.utc)
                    start_time_ms = trade_state['entry_timestamp_ms']
                    end_time_ms = int(close_timestamp_utc.timestamp() * 1000)
                    
                    klines = get_historical_klines(symbol, start_time_ms, end_time_ms)
                    
                    extreme_profit_price = current_price
                    if klines:
                        if direction == 'long': extreme_profit_price = max(float(k[2]) for k in klines)
                        else: extreme_profit_price = min(float(k[3]) for k in klines)
                    logger.info(f"[{symbol}][{trade_id}] Analiza historyczna. Rzeczywiste ekstremum ceny: {extreme_profit_price}")

                    entry_price = trade_state['entry_price']
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

                    trade_data = {
                        "trade_id": trade_id,
                        "timestamp_entry": trade_state['entry_timestamp'],
                        "timestamp_close": close_timestamp_utc.isoformat(),
                        "symbol": symbol,
                        "direction": direction.upper(),
                        "main_result": closed_result,
                        "ob_type": alert_data.get('source', 'N/A'),
                        "risk_amount_price_diff": risk_price_diff,
                        "max_profit_price_diff": max_profit_price_diff,
                        "rr_achieved": rr_achieved,
                        "rr_1_0_achieved": achieved_rr_flags.get("tp_1_0", False),
                        "rr_1_5_achieved": achieved_rr_flags.get("tp_1_5", False),
                        "rr_2_0_achieved": achieved_rr_flags.get("tp_2_0", False),
                        "rr_3_0_achieved": achieved_rr_flags.get("tp_3_0", False),
                        "rr_4_0_achieved": achieved_rr_flags.get("tp_4_0", False),
                        "rr_5_0_achieved": achieved_rr_flags.get("tp_5_0", False),
                    }
                    log_trade_to_bigquery(trade_data)
                    
                    state_manager.remove_trade(trade_id)

            else:
                logger.warning(f"[{symbol}][{trade_id}] Nieznany lub brakujący status: '{status}'. Usuwam.")
                state_manager.remove_trade(trade_id)

        except Exception as e:
            logger.error(f"KRYTYCZNY, NIEPRZEWIDZIANY BŁĄD w pętli dla transakcji [{trade_id}]. Błąd: {e}", exc_info=True)
            state_manager.remove_trade(trade_id)
            continue
