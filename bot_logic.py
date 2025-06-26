# /trading_bot/bot_logic.py (WERSJA FINALNA - Architektura Wielo-Kolekcyjna)

import logging
import requests
import uuid
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timezone

# Importy modułów aplikacji
import state_manager
from bigquery_logger import log_trade_to_bigquery, update_analyzed_trade_in_bigquery
import constants

logger = logging.getLogger(__name__)

# --- FUNKCJE POMOCNICZE ---

def get_latest_klines_batch(symbols: List[str]) -> Dict[str, List[Any]]:
    """Pobiera ostatnią świecę 1-min dla listy symboli w jednej paczce."""
    if not symbols:
        return {}
    
    klines_by_symbol = {}
    # Niestety, API v5 Bybit nie wspiera pobierania klines dla wielu symboli w jednym zapytaniu.
    # Musimy iterować, ale robimy to w jednej, dedykowanej funkcji.
    for symbol in symbols:
        params = {"category": "linear", "symbol": symbol.replace('.P', ''), "interval": "1", "limit": 1}
        try:
            response = requests.get(constants.BYBIT_API_URL_V5_KLINE, params=params, timeout=2)
            response.raise_for_status()
            data = response.json()
            if data.get("retCode") == 0 and data["result"]["list"]:
                klines_by_symbol[symbol] = data["result"]["list"][0]
        except Exception as e:
            logger.warning(f"[{symbol}] Nie udało się pobrać ostatniej świecy kline: {e}")
            continue
    return klines_by_symbol

def get_historical_klines(symbol: str, start_time_ms: int, end_time_ms: int) -> List[List[Any]]:
    """Pobiera dane historyczne do analizy po zamknięciu."""
    params = {
        "category": "linear", "symbol": symbol.replace('.P', ''), "interval": "1",
        "start": start_time_ms, "end": end_time_ms, "limit": 1000
    }
    logger.info(f"[{symbol}] Pobieram historię kline od {start_time_ms} do {end_time_ms}")
    try:
        response = requests.get(constants.BYBIT_API_URL_V5_KLINE, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0 and data["result"]["list"]:
            return list(reversed(data["result"]["list"]))
    except Exception as e:
        logger.error(f"[{symbol}] Błąd przy pobieraniu historii kline: {e}", exc_info=True)
    return []

# --- GŁÓWNE BLOKI LOGIKI ---

def _handle_open_new_positions(klines_data: Dict[str, List[Any]]):
    """Logika otwierania nowych pozycji na podstawie `active_setups`."""
    active_setups = list(state_manager.get_all_active_setups())
    if not active_setups: return
    
    logger.info(f"Sprawdzam {len(active_setups)} setupów pod kątem wejścia.")
    
    for setup_doc in active_setups:
        symbol = setup_doc.id
        try:
            setup_data = setup_doc.to_dict()
            latest_kline = klines_data.get(symbol)
            if not latest_kline: continue

            kline_high = float(latest_kline[2])
            kline_low = float(latest_kline[3])
            
            # Zasada 1: Nie otwieraj nowej pozycji, jeśli jest już otwarta z tego setupu.
            if setup_data.get("is_position_open_on_this_setup", False):
                continue
            
            alert_data = setup_data.get('alert_data', {})
            direction = str(alert_data.get('direction', '')).lower()
            entry_level = float(alert_data['entry'])

            # Zasada 3: Sprawdź, czy po przegranej nastąpił reset ceny.
            is_reset_needed = setup_data.get("is_reset_needed_after_loss", False)
            if is_reset_needed:
                if (direction == 'long' and kline_high > entry_level) or \
                   (direction == 'short' and kline_low < entry_level):
                    state_manager.update_setup_after_price_reset(symbol)
                continue # Czekaj na reset, nie otwieraj teraz pozycji.

            should_open = False
            if direction == 'long' and kline_low <= entry_level: should_open = True
            elif direction == 'short' and kline_high >= entry_level: should_open = True
            
            if should_open:
                entry_attempts = setup_data.get('entry_attempts', 0)
                ob_type = "Fresh OB" if entry_attempts == 0 else "Used OB"
                trade_id = str(uuid.uuid4())
                entry_price = entry_level # Wchodzimy po cenie z alertu
                
                logger.info(f"--- [DECYZJA: WEJŚCIE {ob_type}] --- [{symbol}] | Cena: {entry_price} | ID: {trade_id}")
                
                state_manager.create_open_trade(
                    trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
                    entry_price=entry_price, sl_price=float(alert_data['sl']),
                    tp_price=float(alert_data['tp']), alert_data=alert_data
                )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"[{symbol}] Wadliwy setup. Błąd: {e}. Czekam na nowy alert.")
        except Exception as e:
            logger.error(f"[{symbol}] Błąd podczas sprawdzania wejścia: {e}", exc_info=True)

def _handle_manage_open_trades(klines_data: Dict[str, List[Any]]):
    """Logika monitorowania i zamykania aktywnych transakcji z `open_trades`."""
    open_trades = list(state_manager.get_all_open_trades())
    if not open_trades: return
    
    logger.info(f"Monitoruję {len(open_trades)} otwartych pozycji.")

    for trade_doc in open_trades:
        trade_id = trade_doc.id
        try:
            trade_data = trade_doc.to_dict()
            symbol = trade_data['symbol']
            
            latest_kline = klines_data.get(symbol)
            if not latest_kline: continue
            
            kline_high = float(latest_kline[2])
            kline_low = float(latest_kline[3])
            
            direction, sl_price, tp_price = trade_data['direction'], trade_data['sl_price'], trade_data['tp_price']
            
            closed_result = None
            close_price = float(latest_kline[4])

            if direction == 'long':
                if kline_low <= sl_price: closed_result, close_price = "LOSE", sl_price
                elif kline_high >= tp_price: closed_result, close_price = "WIN", tp_price
            elif direction == 'short':
                if kline_high >= sl_price: closed_result, close_price = "LOSE", sl_price
                elif kline_low <= tp_price: closed_result, close_price = "WIN", tp_price
            
            if closed_result:
                log_and_finalize_trade(trade_data, closed_result, close_price)
                state_manager.remove_open_trade(trade_id)
                state_manager.create_analyzed_trade(trade_data)
                state_manager.update_setup_after_trade_close(symbol, is_loss=(closed_result == "LOSE"))

        except Exception as e:
            logger.error(f"[{trade_id}] Błąd podczas monitorowania otwartej pozycji: {e}", exc_info=True)

def _handle_post_mortem_analysis(klines_data: Dict[str, List[Any]]):
    """Logika pasywnej analizy "duchów" transakcji z `analyzed_trades`."""
    analyzed_trades = list(state_manager.get_all_analyzed_trades())
    if not analyzed_trades: return

    logger.info(f"Analizuję {len(analyzed_trades)} zamkniętych pozycji.")
    
    for trade_doc in analyzed_trades:
        trade_id = trade_doc.id
        try:
            analysis_data = trade_doc.to_dict()
            symbol = analysis_data['symbol']
            
            latest_kline = klines_data.get(symbol)
            if not latest_kline: continue

            kline_high, kline_low = float(latest_kline[2]), float(latest_kline[3])
            
            direction = analysis_data['direction']
            original_sl = analysis_data['original_sl']
            alert_snapshot = analysis_data.get('alert_data_snapshot', {})
            tp5_price = float(alert_snapshot.get('tp_5_0', 0))

            should_remove = False
            if (direction == 'long' and kline_low <= original_sl) or \
               (direction == 'short' and kline_high >= original_sl):
                should_remove = True
            
            if (direction == 'long' and kline_high >= tp5_price) or \
               (direction == 'short' and kline_low <= tp5_price):
                should_remove = True
            
            if should_remove:
                logger.info(f"[{trade_id}] Kończę analizę post-mortem (osiągnięto SL lub TP5).")
                state_manager.remove_analyzed_trade(trade_id)
            else:
                # Tutaj można by dodać logikę aktualizacji BigQuery, jeśli jest potrzebna
                pass

        except Exception as e:
            logger.error(f"[{trade_id}] Błąd podczas analizy post-mortem: {e}", exc_info=True)


def log_and_finalize_trade(trade_data: dict, closed_result: str, close_price: float):
    """Helper do analizy klines i logowania ZAMKNIĘTEJ transakcji do BigQuery."""
    trade_id, symbol, direction = trade_data['trade_id'], trade_data['symbol'], trade_data['direction']
    logger.info(f"--- [ZAMKNIĘCIE: {closed_result}] --- [{symbol}] | ID: {trade_id} | Cena: {close_price}")

    close_timestamp_utc = datetime.now(timezone.utc)
    start_time_ms = trade_data['opened_at_ms']
    
    klines = get_historical_klines(symbol, start_time_ms, int(close_timestamp_utc.timestamp() * 1000))
    
    extreme_profit_price = close_price
    if klines:
        if direction == 'long': extreme_profit_price = max(float(k[2]) for k in klines)
        else: extreme_profit_price = min(float(k[3]) for k in klines)
    logger.info(f"[{symbol}][{trade_id}] Analiza historyczna. Ekstremum ceny: {extreme_profit_price}")

    entry_price, sl_price = trade_data['entry_price'], trade_data['sl_price']
    
    risk_price_diff = abs(entry_price - sl_price)
    max_profit_price_diff = abs(extreme_profit_price - entry_price)
    rr_achieved = (max_profit_price_diff / risk_price_diff) if risk_price_diff > 0 else 0.0
    
    achieved_rr_flags = {}
    alert_snapshot = trade_data.get('alert_data_snapshot', {})
    rr_targets = {k: v for k, v in alert_snapshot.items() if k.startswith('tp_')}
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
        "trade_id": trade_id, "timestamp_entry": trade_data['opened_at_iso'],
        "timestamp_close": close_timestamp_utc.isoformat(), "symbol": symbol,
        "direction": direction.upper(), "main_result": closed_result,
        "ob_type": trade_data['ob_type'], "rr_achieved": rr_achieved,
        **{f"rr_{k.split('_')[1]}_{k.split('_')[2]}_achieved": v for k, v in achieved_rr_flags.items()}
    }
    log_trade_to_bigquery(bq_data)


def run_trading_logic():
    """Główna funkcja orkiestrująca, wywoływana z main.py."""
    
    all_setups = list(state_manager.get_all_active_setups())
    all_open_trades = list(state_manager.get_all_open_trades())
    all_analyzed_trades = list(state_manager.get_all_analyzed_trades())
    
    # Zbierz unikalną listę wszystkich symboli do obserwacji
    symbols_to_watch = set()
    for doc in all_setups: symbols_to_watch.add(doc.id)
    for doc in all_open_trades: symbols_to_watch.add(doc.to_dict()['symbol'])
    for doc in all_analyzed_trades: symbols_to_watch.add(doc.to_dict()['symbol'])

    if not symbols_to_watch:
        logger.info("Brak jakichkolwiek aktywnych operacji do monitorowania.")
        return

    # Pobierz najnowsze dane świecowe dla wszystkich potrzebnych symboli
    latest_klines = get_latest_klines_batch(list(symbols_to_watch))
    
    try:
        _handle_manage_open_trades(latest_klines, all_open_trades)
    except Exception as e:
        logger.error(f"Krytyczny błąd w _handle_manage_open_trades: {e}", exc_info=True)

    try:
        _handle_open_new_positions(latest_klines, all_setups, all_open_trades)
    except Exception as e:
        logger.error(f"Krytyczny błąd w _handle_open_new_positions: {e}", exc_info=True)

    try:
        _handle_post_mortem_analysis(latest_klines, all_analyzed_trades)
    except Exception as e:
        logger.error(f"Krytyczny błąd w _handle_post_mortem_analysis: {e}", exc_info=True)
