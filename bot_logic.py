# W pliku: /bot_logic.py
# ZASTĄP CAŁĄ ZAWARTOŚĆ PLIKU

import logging
import requests
import uuid
import time
from typing import Dict, Any, List, Iterable
from datetime import datetime, timezone, timedelta
from google.cloud.firestore_v1.document import DocumentSnapshot
from pydantic import ValidationError

import state_manager
from bigquery_logger import log_trade_to_bigquery, update_analyzed_trade_in_bigquery
import constants
from models import SetupData, OpenTradeData, AnalyzedTradeData

logger = logging.getLogger(__name__)


def get_historical_klines(symbol: str, start_time_ms: int, end_time_ms: int) -> List[List[Any]]:
    """Pobiera dane historyczne do analizy po zamknięciu."""
    params = {
        "category": "linear", "symbol": symbol.replace('.P', ''), "interval": "1",
        "start": start_time_ms, "end": end_time_ms, "limit": 1000
    }
    logger.debug(f"[{symbol}] Pobieram historię kline od {start_time_ms} do {end_time_ms}")
    try:
        response = requests.get(constants.BYBIT_API_URL_V5_KLINE, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
            return list(reversed(data["result"]["list"]))
    except Exception as e:
        logger.error(f"[{symbol}] Błąd przy pobieraniu historii kline: {e}", exc_info=True)
    return []

def _handle_setups(klines_data: Dict[str, List[Any]], active_setups: List[DocumentSnapshot]):
    """Logika analizująca setupy pod kątem wejścia lub natychmiastowego zamknięcia."""
    if not active_setups: return
    logger.info(f"Sprawdzam {len(active_setups)} aktywnych setupów.")

    for setup_doc in active_setups:
        symbol = setup_doc.id
        try:
            setup = SetupData.parse_obj(setup_doc.to_dict())

            if setup.is_position_open_on_this_setup:
                continue

            latest_kline = klines_data.get(symbol)
            if not latest_kline: continue

            kline_high, kline_low = float(latest_kline[2]), float(latest_kline[3])
            
            direction = setup.alert_data.direction.lower()
            entry_level = setup.alert_data.entry
            sl_price = setup.alert_data.sl
            tp_price = setup.alert_data.tp

            if setup.is_reset_needed_after_loss:
                if (direction == 'long' and kline_high > entry_level) or \
                   (direction == 'short' and kline_low < entry_level):
                    state_manager.update_setup_after_price_reset(symbol)
                continue

            entry_triggered = (direction == 'long' and kline_low <= entry_level) or \
                              (direction == 'short' and kline_high >= entry_level)
            
            if entry_triggered:
                closed_result, close_price = None, entry_level
                
                if direction == 'long':
                    if kline_low <= sl_price: closed_result, close_price = "LOSE", sl_price
                    elif kline_high >= tp_price: closed_result, close_price = "WIN", tp_price
                elif direction == 'short':
                    if kline_high >= sl_price: closed_result, close_price = "LOSE", sl_price
                    elif kline_low <= tp_price: closed_result, close_price = "WIN", tp_price
                
                ob_type = "Fresh OB" if setup.entry_attempts == 0 else "Used OB"
                trade_id = str(uuid.uuid4())

                if closed_result:
                    logger.info(f"--- [WEJŚCIE I ZAMKNIĘCIE W 1 MIN] --- [{symbol}] | Wynik: {closed_result} | ID: {trade_id}")
                    now_utc = datetime.now(timezone.utc)
                    fake_trade = OpenTradeData(
                        trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
                        entry_price=entry_level, sl_price=sl_price, tp_price=tp_price,
                        opened_at_ms=int(now_utc.timestamp() * 1000) - 60000,
                        opened_at_iso=(now_utc - timedelta(minutes=1)).isoformat(),
                        alert_data_snapshot=setup.alert_data.dict(by_alias=True)
                    )
                    log_and_finalize_trade(fake_trade, closed_result, close_price)
                    state_manager.update_setup_after_trade_close(symbol, is_loss=(closed_result == "LOSE"))
                    state_manager.update_setup_entry_attempt(symbol)
                else:
                    logger.info(f"--- [DECYZJA: WEJŚCIE {ob_type}] --- [{symbol}] | Cena: {entry_level} | ID: {trade_id}")
                    state_manager.create_open_trade(
                        trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
                        entry_price=entry_level, sl_price=sl_price, tp_price=tp_price,
                        alert_data=setup.alert_data
                    )
        except ValidationError as e:
            logger.error(f"[{symbol}] Błąd walidacji danych setupu: {e}")
        except Exception as e:
            logger.error(f"[{symbol}] Błąd podczas sprawdzania wejścia: {e}", exc_info=True)

def _handle_manage_open_trades(klines_data: Dict[str, List[Any]], open_trades: List[DocumentSnapshot]):
    if not open_trades: return
    logger.info(f"Monitoruję {len(open_trades)} otwartych pozycji.")
    for trade_doc in open_trades:
        trade_id = trade_doc.id
        try:
            trade = OpenTradeData.parse_obj(trade_doc.to_dict())
            symbol = trade.symbol

            latest_kline = klines_data.get(symbol)
            if not latest_kline: continue
            
            kline_high, kline_low = float(latest_kline[2]), float(latest_kline[3])
            
            closed_result, close_price = None, float(latest_kline[4])
            if trade.direction.lower() == 'long':
                if kline_low <= trade.sl_price: closed_result, close_price = "LOSE", trade.sl_price
                elif kline_high >= trade.tp_price: closed_result, close_price = "WIN", trade.tp_price
            elif trade.direction.lower() == 'short':
                if kline_high >= trade.sl_price: closed_result, close_price = "LOSE", trade.sl_price
                elif kline_low <= trade.tp_price: closed_result, close_price = "WIN", trade.tp_price
            
            if closed_result:
                log_and_finalize_trade(trade, closed_result, close_price)
                state_manager.remove_open_trade(trade_id)
                state_manager.create_analyzed_trade(trade)
                state_manager.update_setup_after_trade_close(symbol, is_loss=(closed_result == "LOSE"))
        except ValidationError as e:
            logger.error(f"[{trade_id}] Błąd walidacji danych otwartej pozycji: {e}")
        except Exception as e:
            logger.error(f"[{trade_id}] Błąd podczas monitorowania otwartej pozycji: {e}", exc_info=True)

def _handle_post_mortem_analysis(klines_data: Dict[str, List[Any]], analyzed_trades: List[DocumentSnapshot]):
    if not analyzed_trades: return
    logger.info(f"Analizuję {len(analyzed_trades)} zamkniętych pozycji (post-mortem).")
    for trade_doc in analyzed_trades:
        trade_id = trade_doc.id
        try:
            analysis = AnalyzedTradeData.parse_obj(trade_doc.to_dict())
            
            latest_kline = klines_data.get(analysis.symbol)
            if latest_kline:
                kline_high, kline_low = float(latest_kline[2]), float(latest_kline[3])
                tp5_price = analysis.alert_data_snapshot.get('tp_5_0')
                
                is_finished = False
                if analysis.direction.lower() == 'long':
                    if kline_low <= analysis.original_sl or (tp5_price and kline_high >= float(tp5_price)): is_finished = True
                else: # short
                    if kline_high >= analysis.original_sl or (tp5_price and kline_low <= float(tp5_price)): is_finished = True

                if is_finished:
                    logger.info(f"[{trade_id}] Analiza post-mortem zakończona (osiągnięto SL/TP5). Usuwam 'ducha'.")
                    state_manager.remove_analyzed_trade(trade_id)
                    continue
            
            if analysis.last_bq_update_iso and (datetime.now(timezone.utc) - analysis.last_bq_update_iso).total_seconds() < 300:
                continue

            now_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            new_klines = get_historical_klines(analysis.symbol, analysis.last_analysis_timestamp_ms, now_ts_ms)
            if not new_klines:
                state_manager.update_analyzed_trade_analysis_timestamp(trade_id, now_ts_ms)
                continue

            current_extreme = analysis.last_known_extreme_price
            if analysis.direction.lower() == 'long':
                new_max = max(float(k[2]) for k in new_klines)
                if new_max > current_extreme: current_extreme = new_max
            else:
                new_min = min(float(k[3]) for k in new_klines)
                if new_min < current_extreme: current_extreme = new_min

            if current_extreme == analysis.last_known_extreme_price:
                state_manager.update_analyzed_trade_analysis_timestamp(trade_id, now_ts_ms)
                continue

            risk_diff = abs(analysis.entry_price - analysis.original_sl)
            profit_diff = abs(current_extreme - analysis.entry_price)
            rr_achieved = (profit_diff / risk_diff) if risk_diff > 0 else 0.0

            updates_for_bq = {"rr_achieved": rr_achieved}
            rr_targets = {k: v for k, v in analysis.alert_data_snapshot.items() if k.startswith('tp_')}
            for rr_key, tp_value in rr_targets.items():
                if tp_value:
                    # ### POPRAWKA ### - Poprawne generowanie nazwy kolumny
                    parts = rr_key.split('_') # np. ['tp', '1', '5']
                    key_name_bq = f"rr_{parts[1]}_{parts[2]}_achieved" # -> rr_1_5_achieved
                    if (analysis.direction.lower() == 'long' and current_extreme >= float(tp_value)) or \
                       (analysis.direction.lower() == 'short' and current_extreme <= float(tp_value)):
                        updates_for_bq[key_name_bq] = True

            if update_analyzed_trade_in_bigquery(trade_id, updates_for_bq):
                state_manager.update_analyzed_trade_timestamp(trade_id)
            state_manager.update_analyzed_trade_analysis_state(trade_id, now_ts_ms, current_extreme)

        except ValidationError as e:
            logger.error(f"[{trade_id}] Błąd walidacji danych 'ducha': {e}")
        except Exception as e:
            logger.error(f"[{trade_id}] Błąd podczas analizy post-mortem: {e}", exc_info=True)

def log_and_finalize_trade(trade: OpenTradeData, closed_result: str, close_price: float):
    logger.info(f"--- [FINALIZACJA I PEŁNA ANALIZA] --- [{trade.symbol}] | ID: {trade.trade_id} | Wynik: {closed_result}")

    close_timestamp_utc = datetime.now(timezone.utc)
    klines = get_historical_klines(trade.symbol, trade.opened_at_ms, int(close_timestamp_utc.timestamp() * 1000))
    
    extreme_profit_price = close_price
    if klines:
        if trade.direction.lower() == 'long':
            extreme_profit_price = max([float(k[2]) for k in klines] + [close_price])
        else:
            extreme_profit_price = min([float(k[3]) for k in klines] + [close_price])
            
    risk_diff = abs(trade.entry_price - trade.sl_price)
    rr_achieved = (abs(extreme_profit_price - trade.entry_price) / risk_diff) if risk_diff > 0 else 0.0
    
    achieved_rr_flags = {}
    rr_targets = {k: v for k, v in trade.alert_data_snapshot.items() if k.startswith('tp_')}
    for rr_key, tp_value in rr_targets.items():
        if tp_value:
            # ### POPRAWKA ### - Poprawne generowanie nazwy flagi
            parts = rr_key.split('_') # np. ['tp', '1', '5']
            flag_name = f"rr_{parts[1]}_{parts[2]}_achieved" # -> rr_1_5_achieved
            if (trade.direction.lower() == 'long' and extreme_profit_price >= float(tp_value)) or \
               (trade.direction.lower() == 'short' and extreme_profit_price <= float(tp_value)):
                achieved_rr_flags[flag_name] = True

    bq_data = {
        "trade_id": trade.trade_id, "timestamp_entry": trade.opened_at_iso,
        "timestamp_close": close_timestamp_utc.isoformat(), "symbol": trade.symbol,
        "direction": trade.direction.upper(), "main_result": closed_result,
        "ob_type": trade.ob_type, "rr_achieved": rr_achieved,
    }
    # ### POPRAWKA ### - Użycie spójnych kluczy do wypełnienia danych dla BQ
    for i in ['1_0', '1_5', '2_0', '3_0', '5_0']:
        key_name = f"rr_{i}_achieved" # -> rr_1_0_achieved, rr_1_5_achieved etc.
        bq_data[key_name] = achieved_rr_flags.get(key_name, False)

    log_trade_to_bigquery(bq_data)

def run_trading_logic():
    logger.info("Rozpoczynam główną pętlę logiki tradingowej.")
    
    all_setups = list(state_manager.get_all_active_setups())
    all_open_trades = list(state_manager.get_all_open_trades())
    all_analyzed_trades = list(state_manager.get_all_analyzed_trades())

    symbols_to_watch = set()
    for doc in all_setups: symbols_to_watch.add(doc.id)
    for doc in all_open_trades:
        if data := doc.to_dict(): symbols_to_watch.add(data.get('symbol'))
    for doc in all_analyzed_trades:
        if data := doc.to_dict(): symbols_to_watch.add(data.get('symbol'))
    symbols_to_watch.discard(None)

    if not symbols_to_watch:
        logger.info("Brak jakichkolwiek aktywnych operacji do monitorowania. Kończę cykl.")
        return

    latest_klines_from_cache = state_manager.get_latest_klines_from_cache(list(symbols_to_watch))
    klines_data_for_handlers = {}
    for symbol, data in latest_klines_from_cache.items():
        klines_data_for_handlers[symbol] = [0, 0, data.get('high'), data.get('low'), data.get('close'), 0, 0]

    try: _handle_setups(klines_data_for_handlers, all_setups)
    except Exception as e: logger.error(f"Krytyczny błąd w _handle_setups: {e}", exc_info=True)
    
    try: _handle_manage_open_trades(klines_data_for_handlers, all_open_trades)
    except Exception as e: logger.error(f"Krytyczny błąd w _handle_manage_open_trades: {e}", exc_info=True)
    
    try: _handle_post_mortem_analysis(klines_data_for_handlers, all_analyzed_trades)
    except Exception as e: logger.error(f"Krytyczny błąd w _handle_post_mortem_analysis: {e}", exc_info=True)

    logger.info("Zakończono główną pętlę logiki tradingowej.")