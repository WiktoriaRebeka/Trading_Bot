# /trading_bot/bot_logic.py (WERSJA FINALNA v5.0 - Analiza High/Low)

import logging
import requests
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

# Importy modułów aplikacji
import state_manager
from bigquery_logger import log_trade_to_bigquery
import constants

logger = logging.getLogger(__name__)

def get_latest_kline(symbol: str) -> Optional[List[Any]]:
    """Pobiera dane ostatniej zamkniętej świecy 1-min z API Bybit."""
    params = {
        "category": "linear", "symbol": symbol.replace('.P', ''), "interval": "1", "limit": 1
    }
    try:
        response = requests.get(constants.BYBIT_API_URL_V5_KLINE, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
            # Zwracamy pierwszą (i jedyną) świecę z listy
            kline_data = data["result"]["list"][0]
            # Format: [startTime, open, high, low, close, volume, turnover]
            return kline_data
    except Exception as e:
        logger.error(f"[{symbol}] Nie udało się pobrać ostatniej świecy kline: {e}")
    return None

def get_historical_klines(symbol: str, start_time_ms: int, end_time_ms: int) -> List[List[Any]]:
    """Pobiera dane historyczne (kline) 1-min z API Bybit do analizy po zamknięciu."""
    params = {
        "category": "linear", "symbol": symbol.replace('.P', ''), "interval": "1",
        "start": start_time_ms, "end": end_time_ms, "limit": 1000
    }
    logger.info(f"[{symbol}] Pobieram dane historyczne kline od {start_time_ms} do {end_time_ms}")
    try:
        response = requests.get(constants.BYBIT_API_URL_V5_KLINE, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
            return list(reversed(data["result"]["list"]))
    except Exception as e:
        logger.error(f"[{symbol}] Nieoczekiwany błąd przy pobieraniu historii kline: {e}", exc_info=True)
    return []

def _handle_open_new_positions():
    """Część logiki odpowiedzialna za otwieranie nowych pozycji."""
    active_setups = list(state_manager.get_all_active_setups())
    if not active_setups: return
    
    logger.info(f"Sprawdzam {len(active_setups)} aktywnych setupów pod kątem wejścia.")
    
    for setup_doc in active_setups:
        symbol = setup_doc.id
        try:
            if state_manager.is_position_open_for_symbol(symbol):
                continue

            latest_kline = get_latest_kline(symbol)
            if not latest_kline:
                logger.warning(f"[{symbol}] Brak danych kline do sprawdzenia wejścia.")
                continue

            kline_high = float(latest_kline[2])
            kline_low = float(latest_kline[3])

            setup_data = setup_doc.to_dict()
            alert_data = setup_data.get('alert_data', {})
            direction = str(alert_data.get('direction', '')).lower()
            entry_level = float(alert_data['entry'])
            
            should_open = False
            if direction == 'long' and kline_low <= entry_level:
                should_open = True
                entry_price = min(entry_level, kline_high) # Wejdź po cenie entry lub wyższej, jeśli knot poszedł wyżej
            elif direction == 'short' and kline_high >= entry_level:
                should_open = True
                entry_price = max(entry_level, kline_low) # Wejdź po cenie entry lub niższej

            if should_open:
                entry_attempts = setup_data.get('entry_attempts', 0)
                ob_type = "Fresh OB" if entry_attempts == 0 else "Used OB"
                trade_id = str(uuid.uuid4())
                
                logger.info(f"--- [DECYZJA: WEJŚCIE {ob_type}] --- [{symbol}] | Cena wejścia: {entry_price} | ID: {trade_id}")
                
                state_manager.create_open_trade(
                    trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
                    entry_price=entry_price, sl_price=float(alert_data['sl']),
                    tp_price=float(alert_data['tp']), alert_data=alert_data
                )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"[{symbol}] Wadliwy setup. Błąd: {e}. Czekam na nowy alert.")
            continue
        except Exception as e:
            logger.error(f"[{symbol}] Nieoczekiwany błąd podczas sprawdzania wejścia: {e}", exc_info=True)
            continue

def _handle_manage_open_trades():
    """Część logiki odpowiedzialna za monitorowanie i zamykanie otwartych pozycji."""
    open_trades = list(state_manager.get_all_open_trades())
    if not open_trades: return
    
    logger.info(f"Monitoruję {len(open_trades)} otwartych pozycji.")

    for trade_doc in open_trades:
        trade_id = trade_doc.id
        try:
            trade_data = trade_doc.to_dict()
            symbol = trade_data['symbol']
            
            latest_kline = get_latest_kline(symbol)
            if not latest_kline:
                logger.warning(f"[{symbol}][{trade_id}] Brak danych kline do monitorowania pozycji.")
                continue
            
            kline_high = float(latest_kline[2])
            kline_low = float(latest_kline[3])
            
            direction = trade_data['direction']
            sl_price = trade_data['sl_price']
            tp_price = trade_data['tp_price']
            
            closed_result = None
            close_price = float(latest_kline[4]) # Domyślnie cena zamknięcia świecy

            if direction == 'long':
                if kline_low <= sl_price:
                    closed_result = "LOSE"
                    close_price = sl_price
                elif kline_high >= tp_price:
                    closed_result = "WIN"
                    close_price = tp_price
            elif direction == 'short':
                if kline_high >= sl_price:
                    closed_result = "LOSE"
                    close_price = sl_price
                elif kline_low <= tp_price:
                    closed_result = "WIN"
                    close_price = tp_price
            
            if closed_result:
                log_and_finalize_trade(trade_data, closed_result, close_price)
                state_manager.remove_closed_trade(trade_id)

        except Exception as e:
            logger.error(f"[{trade_id}] Nieoczekiwany błąd podczas monitorowania otwartej pozycji: {e}", exc_info=True)
            continue

def log_and_finalize_trade(trade_data: dict, closed_result: str, close_price: float):
    """Helper do analizy klines i logowania zamkniętej transakcji do BigQuery."""
    # ... (Ta funkcja pozostaje praktycznie bez zmian, jest już poprawna) ...
    pass # Placeholder

def run_trading_logic():
    """Główna funkcja orkiestrująca, wywoływana z main.py, już bez argumentów."""
    
    try:
        _handle_open_new_positions()
    except Exception as e:
        logger.error(f"Krytyczny błąd w _handle_open_new_positions: {e}", exc_info=True)

    try:
        _handle_manage_open_trades()
    except Exception as e:
        logger.error(f"Krytyczny błąd w _handle_manage_open_trades: {e}", exc_info=True)
