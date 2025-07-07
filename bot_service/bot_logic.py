# Lokalizacja: bot_service/bot_logic.py
# WERSJA PRODUKCYJNA - FINALNA I KOMPLETNA v3

import logging
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from google.cloud.firestore_v1.document import DocumentSnapshot
from pydantic import ValidationError
import asyncio
import aiohttp

from shared_lib.firebase_client import get_db
from bot_service import state_manager
from bot_service.bigquery_logger import log_trade_to_bigquery, update_analyzed_trade_in_bigquery
from shared_lib import constants
from shared_lib.models import SetupData, OpenTradeData, AnalyzedTradeData, AlertData

logger = logging.getLogger(__name__)

# --- Funkcje pomocnicze (bez zmian) ---

def process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]]):
    if not newly_fetched_alerts: return
    logger.info(f"Przetwarzam {len(newly_fetched_alerts)} nowych alertów.")
    db = get_db()
    for alert_dict in newly_fetched_alerts:
        try:
            alert_data = AlertData.model_validate(alert_dict)
            if alert_data.direction_code == 1: alert_data.direction = "LONG"
            elif alert_data.direction_code == -1: alert_data.direction = "SHORT"
            else:
                logger.warning(f"Pominięto alert z nieznanym direction_code: {alert_data.direction_code}")
                continue
            new_setup = SetupData(alert_data=alert_data, updated_at=datetime.now(timezone.utc))
            doc_ref = db.collection(constants.SETUP_COLLECTION).document(alert_data.symbol)
            doc_ref.set(new_setup.model_dump(by_alias=True))
            logger.info(f"[{alert_data.symbol}] Zarejestrowano/zaktualizowano aktywny setup.")
        except ValidationError as e: logger.error(f"Błąd walidacji alertu. ID: {alert_dict.get('id')}. Błędy: {e}")
        except Exception as e: logger.error(f"Nieoczekiwany błąd podczas przetwarzania alertu ID: {alert_dict.get('id')}: {e}", exc_info=True)

def _calculate_trade_analytics(direction: str, entry_price: float, sl_price: float, extreme_price: float, alert_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    risk_diff = abs(entry_price - sl_price)
    if risk_diff > 0:
        profit_diff = abs(extreme_price - entry_price)
        rr_achieved = round(profit_diff / risk_diff, 4)
    else:
        rr_achieved = 0.0
        logger.warning(f"Różnica ryzyka wynosi zero (entry={entry_price}, sl={sl_price}). Ustawiono rr_achieved na 0.0.")
    analytics_results = {"rr_achieved": rr_achieved}
    rr_thresholds = {"rr_1_0_achieved": 1.0, "rr_1_5_achieved": 1.5, "rr_2_0_achieved": 2.0, "rr_3_0_achieved": 3.0, "rr_4_0_achieved": 4.0, "rr_5_0_achieved": 5.0}
    for flag_name, threshold in rr_thresholds.items():
        analytics_results[flag_name] = rr_achieved >= threshold
    return analytics_results

async def get_historical_klines(session: aiohttp.ClientSession, symbol: str, start_time_ms: int, end_time_ms: int) -> List[List[Any]]:
    params = {"category": "linear", "symbol": symbol.replace('.P', ''), "interval": "1", "start": start_time_ms, "end": end_time_ms, "limit": 1000}
    logger.debug(f"[{symbol}] Pobieram historię kline od {start_time_ms} do {end_time_ms}")
    try:
        async with session.get(constants.BYBIT_API_URL_V5_KLINE, params=params, timeout=10) as response:
            response.raise_for_status()
            data = await response.json()
            if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
                return list(reversed(data["result"]["list"]))
    except Exception as e:
        logger.error(f"[{symbol}] Błąd przy pobieraniu historii kline: {e}", exc_info=True)
    return []

# --- Główne funkcje logiki z POPRAWKAMI ---

async def log_and_finalize_trade(session: aiohttp.ClientSession, trade: OpenTradeData, closed_result: str, close_price: float):
    logger.info(f"--- [FINALIZACJA I PEŁNA ANALIZA] --- [{trade.symbol}] | ID: {trade.trade_id} | Wynik: {closed_result}")
    close_timestamp_utc = datetime.now(timezone.utc)
    klines = await get_historical_klines(session, trade.symbol, trade.opened_at_ms, int(close_timestamp_utc.timestamp() * 1000))
    extreme_profit_price = close_price
    if klines:
        if trade.direction.lower() == 'long':
            extreme_profit_price = max([float(k[2]) for k in klines] + [close_price])
        else:
            extreme_profit_price = min([float(k[3]) for k in klines] + [close_price])
    analytics_data = _calculate_trade_analytics(trade.direction, trade.entry_price, trade.sl_price, extreme_profit_price, trade.alert_data_snapshot)
    bq_data = {"trade_id": trade.trade_id, "timestamp_entry": trade.opened_at_iso, "timestamp_close": close_timestamp_utc.isoformat(), "symbol": trade.symbol, "direction": trade.direction.upper(), "main_result": closed_result, "ob_type": trade.ob_type}
    bq_data.update(analytics_data)
    log_trade_to_bigquery(bq_data)
    state_manager.create_analyzed_trade(trade, extreme_profit_price)

def _handle_setups(klines_data: Dict[str, List[Any]], active_setups: List[DocumentSnapshot]) -> List[Dict[str, Any]]:
    if not active_setups: return []
    logger.info(f"Sprawdzam {len(active_setups)} aktywnych setupów.")
    trades_to_finalize_immediately = []
    for setup_doc in active_setups:
        symbol = setup_doc.id
        try:
            setup = SetupData.model_validate(setup_doc.to_dict())
            if setup.is_position_open_on_this_setup: continue
            latest_kline = klines_data.get(symbol)
            if not latest_kline: continue
            kline_high, kline_low = float(latest_kline[2]), float(latest_kline[3])
            direction = setup.alert_data.direction.lower()
            entry_level, sl_price, tp_price = setup.alert_data.entry, setup.alert_data.sl, setup.alert_data.tp
            if setup.is_reset_needed_after_loss:
                if (direction == 'long' and kline_high > entry_level) or (direction == 'short' and kline_low < entry_level):
                    state_manager.update_setup_after_price_reset(symbol)
                continue
            entry_triggered = (direction == 'long' and kline_low <= entry_level) or (direction == 'short' and kline_high >= entry_level)
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
                    fake_trade = OpenTradeData(trade_id=trade_id, symbol=symbol, direction=direction.upper(), ob_type=ob_type, entry_price=entry_level, sl_price=sl_price, tp_price=tp_price, opened_at_ms=int(now_utc.timestamp() * 1000) - 60000, opened_at_iso=(now_utc - timedelta(minutes=1)).isoformat(), alert_data_snapshot=setup.alert_data.model_dump(by_alias=True))
                    trades_to_finalize_immediately.append({"trade": fake_trade, "result": closed_result, "price": close_price})
                    state_manager.update_setup_after_trade_close(symbol, is_loss=(closed_result == "LOSE"))
                    state_manager.update_setup_entry_attempt(symbol)
                else:
                    logger.info(f"--- [DECYZJA: WEJŚCIE {ob_type}] --- [{symbol}] | Cena: {entry_level} | ID: {trade_id}")
                    state_manager.create_open_trade(trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type, entry_price=entry_level, sl_price=sl_price, tp_price=tp_price, alert_data=setup.alert_data)
        except ValidationError as e: logger.error(f"[{symbol}] Błąd walidacji danych setupu: {e}")
        except Exception as e: logger.error(f"[{symbol}] Błąd podczas sprawdzania wejścia: {e}", exc_info=True)
    return trades_to_finalize_immediately

async def _handle_manage_open_trades(session: aiohttp.ClientSession, klines_data: Dict[str, List[Any]], open_trades: List[DocumentSnapshot]):
    if not open_trades: return
    logger.info(f"Monitoruję {len(open_trades)} otwartych pozycji.")
    tasks_to_run = []
    for trade_doc in open_trades:
        trade_id = trade_doc.id
        try:
            trade = OpenTradeData.model_validate(trade_doc.to_dict())
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
                tasks_to_run.append(log_and_finalize_trade(session, trade, closed_result, close_price))
                state_manager.remove_open_trade(trade_id)
                state_manager.update_setup_after_trade_close(symbol, is_loss=(closed_result == "LOSE"))
        except ValidationError as e: logger.error(f"[{trade_id}] Błąd walidacji danych otwartej pozycji: {e}")
        except Exception as e: logger.error(f"[{trade_id}] Błąd podczas monitorowania otwartej pozycji: {e}", exc_info=True)
    if tasks_to_run: await asyncio.gather(*tasks_to_run)

async def _analyze_single_ghost(session: aiohttp.ClientSession, analysis_trade: AnalyzedTradeData):
    trade_id = analysis_trade.trade_id
    now_ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    new_klines = await get_historical_klines(session, analysis_trade.symbol, analysis_trade.last_analysis_timestamp_ms, now_ts_ms)
    if not new_klines:
        state_manager.update_analyzed_trade_timestamp_only(trade_id, now_ts_ms)
        return
    extreme_price_in_new_klines = 0.0
    is_sl_hit = False
    if analysis_trade.direction.lower() == 'long':
        extreme_price_in_new_klines = max(float(k[2]) for k in new_klines)
        if min(float(k[3]) for k in new_klines) <= analysis_trade.original_sl: is_sl_hit = True
    else: # short
        extreme_price_in_new_klines = min(float(k[3]) for k in new_klines)
        if max(float(k[2]) for k in new_klines) >= analysis_trade.original_sl: is_sl_hit = True
    
    new_extreme = analysis_trade.last_known_extreme_price
    should_update_bq = False
    if (analysis_trade.direction.lower() == 'long' and extreme_price_in_new_klines > new_extreme) or \
       (analysis_trade.direction.lower() == 'short' and extreme_price_in_new_klines < new_extreme):
        new_extreme = extreme_price_in_new_klines
        should_update_bq = True
    else:
        state_manager.update_analyzed_trade_timestamp_only(trade_id, now_ts_ms)
        return
        
    logger.info(f"[{trade_id}] Nowe ekstremum dla 'ducha': {new_extreme}. Aktualizuję analitykę.")
    updates_for_bq = _calculate_trade_analytics(analysis_trade.direction, analysis_trade.entry_price, analysis_trade.original_sl, new_extreme, analysis_trade.alert_data_snapshot)
    updates_to_send = {k: v for k, v in updates_for_bq.items() if v is True or k == 'rr_achieved'}
    if updates_to_send and update_analyzed_trade_in_bigquery(trade_id, updates_to_send):
        state_manager.update_analyzed_trade_bq_timestamp(trade_id)
    
    state_manager.update_analyzed_trade_analysis_state(trade_id, now_ts_ms, new_extreme)
    
    is_tp5_hit = analysis_trade.original_tp_5_0 and \
                ((analysis_trade.direction.lower() == 'long' and new_extreme >= analysis_trade.original_tp_5_0) or \
                 (analysis_trade.direction.lower() == 'short' and new_extreme <= analysis_trade.original_tp_5_0))

    if is_sl_hit or is_tp5_hit:
        reason = "osiągnięto SL" if is_sl_hit else "osiągnięto TP5"
        logger.info(f"[{trade_id}] Analiza 'ducha' zakończona ({reason}). Usuwam.")
        state_manager.remove_analyzed_trade(trade_id)

async def _handle_post_mortem_analysis(session: aiohttp.ClientSession, analyzed_trades: List[DocumentSnapshot]):
    if not analyzed_trades: return
    logger.info(f"Analizuję {len(analyzed_trades)} zamkniętych pozycji (post-mortem).")
    tasks = []
    for trade_doc in analyzed_trades:
        try:
            analysis_trade = AnalyzedTradeData.model_validate(trade_doc.to_dict())
            tasks.append(_analyze_single_ghost(session, analysis_trade))
        except ValidationError as e:
            logger.error(f"[{trade_doc.id}] Błąd walidacji danych 'ducha', pomijam: {e}")
    if tasks: await asyncio.gather(*tasks, return_exceptions=True)

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
    
    valid_symbols_to_watch = {s for s in symbols_to_watch if isinstance(s, str) and s}
    if len(valid_symbols_to_watch) != len(symbols_to_watch):
        logger.warning(f"Odrzucono nieprawidłowe symbole: {symbols_to_watch - valid_symbols_to_watch}")
    if not valid_symbols_to_watch:
        logger.info("Brak poprawnych symboli do monitorowania. Kończę cykl.")
        return

    latest_klines_from_cache = state_manager.get_latest_klines_from_cache(list(valid_symbols_to_watch))
    if not latest_klines_from_cache:
        logger.warning("Nie udało się pobrać danych z cache'u klines. Nie można kontynuować.")
        return
    klines_data_for_handlers = {symbol: [0, 0, data['high'], data['low'], data['close'], 0, 0] for symbol, data in latest_klines_from_cache.items() if 'high' in data and 'low' in data and 'close' in data}
    
    async def async_main():
        async with aiohttp.ClientSession() as session:
            tasks = []
            immediate_finalization_jobs = _handle_setups(klines_data_for_handlers, all_setups)
            tasks.extend([log_and_finalize_trade(session, job["trade"], job["result"], job["price"]) for job in immediate_finalization_jobs])
            tasks.append(_handle_manage_open_trades(session, klines_data_for_handlers, all_open_trades))
            tasks.append(_handle_post_mortem_analysis(session, all_analyzed_trades))
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Wystąpił błąd podczas zadania asynchronicznego nr {i}: {result}", exc_info=True)
    try:
        asyncio.run(async_main())
    except Exception as e:
        logger.error(f"Błąd podczas uruchamiania pętli asyncio: {e}", exc_info=True)
    logger.info("Zakończono główną pętlę logiki tradingowej.")
