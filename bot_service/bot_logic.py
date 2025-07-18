# Lokalizacja: bot_service/bot_logic.py

import logging
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from google.cloud import firestore
from google.cloud.firestore_v1.document import DocumentSnapshot
from pydantic import ValidationError
import asyncio

from shared_lib.firebase_client import get_db, get_symbols_to_watch_from_config
from bot_service import state_manager
from bot_service.bigquery_logger import log_trade_to_bigquery
from shared_lib import constants
from shared_lib.models import SetupData, OpenTradeData, AnalyzedTradeData, AlertData, Kline

logger = logging.getLogger(__name__)

def _calculate_rr_analytics(entry_price: float, sl_price: float, extreme_price: float, direction: str) -> Dict[str, Any]:
    risk_diff = abs(entry_price - sl_price)
    if risk_diff == 0:
        logger.warning(f"Różnica ryzyka wynosi zero (entry={entry_price}, sl={sl_price}). R:R ustawione na 0.")
        return {"rr_achieved": 0.0}
    profit_diff = 0.0
    if direction.upper() == 'LONG' and extreme_price > entry_price:
        profit_diff = extreme_price - entry_price
    elif direction.upper() == 'SHORT' and extreme_price < entry_price:
        profit_diff = entry_price - extreme_price
    rr_achieved = round(profit_diff / risk_diff, 4)
    analytics = {"rr_achieved": rr_achieved}
    rr_thresholds = {
        "rr_1_0_achieved": 1.0, "rr_1_5_achieved": 1.5, "rr_2_0_achieved": 2.0,
        "rr_3_0_achieved": 3.0, "rr_4_0_achieved": 4.0, "rr_5_0_achieved": 5.0
    }
    for flag, threshold in rr_thresholds.items():
        analytics[flag] = rr_achieved >= threshold
    return analytics

def process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]]):
    if not newly_fetched_alerts: return
    logger.info(f"Przetwarzam {len(newly_fetched_alerts)} nowych alertów.")
    db = get_db()
    for alert_dict in newly_fetched_alerts:
        try:
            alert_data = AlertData.model_validate(alert_dict)
            new_setup = SetupData(
                alert_data=alert_data,
                updated_at=datetime.now(timezone.utc)
            )
            doc_ref = db.collection(constants.SETUP_COLLECTION).document(alert_data.symbol)
            doc_ref.set(new_setup.model_dump(by_alias=True))
            logger.info(f"[{alert_data.symbol}] Zarejestrowano/zaktualizowano aktywny setup.")
        except ValidationError as e:
            logger.error(f"Błąd walidacji alertu. ID: {alert_dict.get('id')}. Błędy: {e}")
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd podczas przetwarzania alertu ID: {alert_dict.get('id')}: {e}", exc_info=True)

def finalize_trade(trade: OpenTradeData, closed_result: str, close_price: float):
    """Finalizuje transakcję: zapisuje do BQ (jeśli LOSE) lub tworzy 'ducha' (jeśli WIN)."""
    logger.info(f"--- [FINALIZACJA] --- [{trade.symbol}] | ID: {trade.trade_id} | Wynik: {closed_result}")
    
    analytics_data = _calculate_rr_analytics(trade.entry_price, trade.sl_price, close_price, trade.direction)
    
    bq_data = {
        "trade_id": trade.trade_id, 
        "timestamp_entry": trade.opened_at_iso,
        "timestamp_close": datetime.now(timezone.utc).isoformat(), 
        "symbol": trade.symbol,
        "direction": trade.direction.upper(), 
        "main_result": closed_result, 
        "ob_type": trade.ob_type
    }
    bq_data.update(analytics_data)

    if closed_result == "LOSE":
        logger.info(f"[{trade.trade_id}] Pozycja przegrana. Zapisuję bezpośrednio do BigQuery.")
        log_trade_to_bigquery(bq_data)
    elif closed_result == "WIN":
        logger.info(f"[{trade.trade_id}] Pozycja wygrana. Zapisuję WSTĘPNY rekord do BQ i tworzę 'ducha'.")
        log_trade_to_bigquery(bq_data)
        state_manager.create_analyzed_trade(trade)

def _handle_setups(klines_data: Dict[str, Kline], active_setups: List[DocumentSnapshot]):
    """Przetwarza aktywne setupy w poszukiwaniu wejść."""
    if not active_setups: return
    logger.info(f"Sprawdzam {len(active_setups)} aktywnych setupów.")

    for setup_doc in active_setups:
        symbol = setup_doc.id
        try:
            setup = SetupData.model_validate(setup_doc.to_dict())
            if setup.is_position_open_on_this_setup:
                continue
            
            latest_kline = klines_data.get(symbol)
            if not latest_kline:
                continue

            direction = setup.alert_data.direction
            entry_level = setup.alert_data.entry
            sl_price = setup.alert_data.sl
            tp_price = setup.alert_data.tp

            if setup.is_reset_needed_after_loss:
                reset_condition_met = (direction == 'LONG' and latest_kline.high > entry_level) or \
                                      (direction == 'SHORT' and latest_kline.low < entry_level)
                if reset_condition_met:
                    logger.info(f"[{symbol}] Warunek resetu ceny spełniony. Setup gotowy do nowego wejścia od następnego cyklu.")
                    state_manager.update_setup_after_price_reset(symbol)
                continue

            entry_triggered = (direction == 'LONG' and latest_kline.low <= entry_level) or \
                              (direction == 'SHORT' and latest_kline.high >= entry_level)

            if entry_triggered:
                closed_result, close_price = None, None
                if direction == 'LONG':
                    if latest_kline.low <= sl_price: closed_result, close_price = "LOSE", sl_price
                    elif latest_kline.high >= tp_price: closed_result, close_price = "WIN", tp_price
                elif direction == 'SHORT':
                    if latest_kline.high >= sl_price: closed_result, close_price = "LOSE", sl_price
                    elif latest_kline.low <= tp_price: closed_result, close_price = "WIN", tp_price

                ob_type = "Fresh OB" if setup.entry_attempts == 0 else "Used OB"
                trade_id = str(uuid.uuid4())

                if closed_result:
                    logger.info(f"--- [WEJŚCIE I ZAMKNIĘCIE W 1 MIN] --- [{symbol}] | Wynik: {closed_result} | ID: {trade_id}")
                    entry_timestamp = datetime.fromtimestamp(latest_kline.timestamp / 1000, tz=timezone.utc)
                    fake_trade = OpenTradeData(
                        trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
                        entry_price=entry_level, sl_price=sl_price, tp_price=tp_price,
                        opened_at_ms=latest_kline.timestamp, opened_at_iso=entry_timestamp.isoformat(),
                        alert_data_snapshot=setup.alert_data.model_dump(by_alias=True)
                    )
                    finalize_trade(fake_trade, closed_result, close_price)
                    
                    try:
                        db = get_db()
                        transaction = db.transaction()
                        state_manager.update_setup_after_immediate_close_transactional(transaction, symbol, is_loss=(closed_result == "LOSE"))
                        logger.info(f"[{symbol}] Transakcja natychmiastowego zamknięcia zakończona pomyślnie.")
                    except Exception as ex:
                        logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD TRANSAKCJI NATYCHMIASTOWEGO ZAMKNIĘCIA: {ex}", exc_info=True)

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

def _handle_manage_open_trades(klines_data: Dict[str, Kline], open_trades: List[DocumentSnapshot]):
    """
    Zarządza otwartymi pozycjami, sprawdza warunki zamknięcia i inicjuje proces finalizacji.
    Używa transakcji Firestore, aby zapewnić spójność danych.
    """
    if not open_trades:
        return

    logger.info(f"Zarządzam {len(open_trades)} otwartymi pozycjami.")

    for trade_doc in open_trades:
        trade_id = trade_doc.id
        try:
            trade = OpenTradeData.model_validate(trade_doc.to_dict())
            latest_kline = klines_data.get(trade.symbol)

            if not latest_kline:
                logger.warning(f"[{trade_id}] Brak danych kline z cache'u dla symbolu {trade.symbol}. Pomijam tę pozycję w cyklu.")
                continue

            closed_result, close_price = None, None
            if trade.direction == 'LONG':
                if latest_kline.low <= trade.sl_price: closed_result, close_price = "LOSE", trade.sl_price
                elif latest_kline.high >= trade.tp_price: closed_result, close_price = "WIN", trade.tp_price
            elif trade.direction == 'SHORT':
                if latest_kline.high >= trade.sl_price: closed_result, close_price = "LOSE", trade.sl_price
                elif latest_kline.low <= trade.tp_price: closed_result, close_price = "WIN", trade.tp_price

            if closed_result:
                logger.info(f"--- [DECYZJA: ZAMKNIĘCIE] --- [{trade.symbol}] | ID: {trade_id} | Wynik: {closed_result}")
                finalize_trade(trade, closed_result, close_price)
                
                try:
                    db = get_db()
                    transaction = db.transaction()
                    state_manager.close_trade_transactional(transaction, trade.trade_id, trade.symbol, is_loss=(closed_result == "LOSE"))
                    logger.info(f"[{trade_id}] Transakcja zamknięcia pozycji i resetu setupu zakończona pomyślnie.")
                except Exception as ex:
                    logger.critical(f"[{trade_id}] KRYTYCZNY BŁĄD TRANSAKCJI ZAMKNIĘCIA: {ex}", exc_info=True)

        except ValidationError as e:
            logger.error(f"[{trade_id}] Błąd walidacji danych otwartej pozycji: {e}")
        except Exception as e:
            logger.error(f"[{trade_id}] Nieoczekiwany błąd podczas monitorowania otwartej pozycji: {e}", exc_info=True)

def _handle_post_mortem_analysis(analyzed_trades: List[DocumentSnapshot], klines_data: Dict[str, Kline]):
    if not analyzed_trades: return
    logger.info(f"[ANALIZA DUCHA] Analiza dla {len(analyzed_trades)} 'duchów'.")
    from bot_service.bigquery_logger import update_analyzed_trade_in_bigquery
    for trade_doc in analyzed_trades:
        trade_id = trade_doc.id
        try:
            analysis_trade = AnalyzedTradeData.model_validate(trade_doc.to_dict())
            symbol = analysis_trade.symbol
            latest_kline = klines_data.get(symbol)
            if not latest_kline:
                logger.warning(f"[ANALIZA DUCHA][{trade_id}] Brak danych kline dla {symbol}.")
                continue
            current_extreme = analysis_trade.last_known_extreme_price
            new_extreme = current_extreme
            if analysis_trade.direction == 'LONG' and latest_kline.high > current_extreme: new_extreme = latest_kline.high
            elif analysis_trade.direction == 'SHORT' and latest_kline.low < current_extreme: new_extreme = latest_kline.low
            if new_extreme != current_extreme:
                state_manager.update_analyzed_trade_state(trade_id, new_extreme, latest_kline.timestamp)
                analysis_trade.last_known_extreme_price = new_extreme
            is_analysis_finished, reason = False, ""
            if analysis_trade.direction == 'LONG':
                if latest_kline.low <= analysis_trade.original_sl: is_analysis_finished, reason = True, "osiągnięto SL"
                elif analysis_trade.original_tp_5_0 and latest_kline.high >= analysis_trade.original_tp_5_0: is_analysis_finished, reason = True, "osiągnięto TP5"
            else:
                if latest_kline.high >= analysis_trade.original_sl: is_analysis_finished, reason = True, "osiągnięto SL"
                elif analysis_trade.original_tp_5_0 and latest_kline.low <= analysis_trade.original_tp_5_0: is_analysis_finished, reason = True, "osiągnięto TP5"
            if is_analysis_finished:
                logger.info(f"[ANALIZA DUCHA][{trade_id}] Koniec analizy ({reason}). Aktualizacja BQ.")
                final_analytics = _calculate_rr_analytics(analysis_trade.entry_price, analysis_trade.original_sl, analysis_trade.last_known_extreme_price, analysis_trade.direction)
                final_updates = {"timestamp_close": datetime.now(timezone.utc).isoformat()}
                final_updates.update(final_analytics)
                update_analyzed_trade_in_bigquery(trade_id, final_updates)
                state_manager.remove_analyzed_trade(trade_id)
        except Exception as e: 
            logger.error(f"[ANALIZA DUCHA][{trade_id}] Błąd: {e}", exc_info=True)

def run_trading_logic():
    logger.info("Rozpoczynam główną pętlę logiki tradingowej.")

    symbols_to_watch = set(get_symbols_to_watch_from_config())
    open_trades_docs = list(state_manager.get_all_open_trades())
    analyzed_trades_docs = list(state_manager.get_all_analyzed_trades())
    
    for doc in open_trades_docs:
        if data := doc.to_dict(): symbols_to_watch.add(data.get('symbol'))
    for doc in analyzed_trades_docs:
        if data := doc.to_dict(): symbols_to_watch.add(data.get('symbol'))

    valid_symbols = {s for s in symbols_to_watch if isinstance(s, str) and s}
    if not valid_symbols:
        logger.info("Brak poprawnych symboli do monitorowania. Kończę cykl.")
        return

    klines_data_from_cache = state_manager.get_latest_klines_from_cache(list(valid_symbols))
    if not klines_data_from_cache:
        logger.warning("Nie udało się pobrać danych z cache'u klines. Nie można kontynuować cyklu.")
        return

    klines_data = {
        symbol: Kline.model_validate(data) for symbol, data in klines_data_from_cache.items()
    }
    
    active_setups_docs = list(state_manager.get_all_active_setups())

    _handle_post_mortem_analysis(analyzed_trades_docs, klines_data)
    
    _handle_setups(klines_data, active_setups_docs)
    _handle_manage_open_trades(klines_data, open_trades_docs)

    logger.info("Zakończono główną pętlę logiki tradingowej.")