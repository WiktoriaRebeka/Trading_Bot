# Lokalizacja: bot_service/bot_logic.py

import logging
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from google.cloud.firestore_v1.document import DocumentSnapshot
from pydantic import ValidationError
import asyncio
import aiohttp

from shared_lib.firebase_client import get_db, get_symbols_to_watch_from_config
from bot_service import state_manager
from bot_service.bigquery_logger import log_trade_to_bigquery, update_analyzed_trade_in_bigquery
from shared_lib import constants
from shared_lib.models import SetupData, OpenTradeData, AnalyzedTradeData, AlertData, Kline

logger = logging.getLogger(__name__)

def _calculate_rr_analytics(entry_price: float, sl_price: float, extreme_price: float, direction: str) -> Dict[str, Any]:
    """Kalkuluje osiągnięte R:R i flagi dla poszczególnych poziomów TP."""
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
    """Przetwarza nowe alerty i zapisuje je jako aktywne setupy w Firestore."""
    if not newly_fetched_alerts:
        return
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

async def get_historical_klines(session: aiohttp.ClientSession, symbol: str, start_time_ms: int, limit: int = 200) -> List[Kline]:
    """Pobiera historię świec od zadanego czasu, zwracając listę obiektów Kline."""
    params = {
        "category": "linear", "symbol": symbol.replace('.P', ''), 
        "interval": "1", "start": start_time_ms, "limit": limit
    }
    logger.debug(f"[{symbol}] Pobieram historię kline od {start_time_ms} z limitem {limit}")
    try:
        async with session.get(constants.BYBIT_API_URL_V5_KLINE, params=params, timeout=10) as response:
            response.raise_for_status()
            data = await response.json()
            if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
                klines = [
                    Kline(timestamp=int(k[0]), high=float(k[2]), low=float(k[3]), close=float(k[4]))
                    for k in reversed(data["result"]["list"])
                ]
                return klines
    except Exception as e:
        logger.error(f"[{symbol}] Błąd przy pobieraniu historii kline: {e}", exc_info=True)
    return []


async def finalize_trade(trade: OpenTradeData, closed_result: str, close_price: float):
    """Finalizuje transakcję: zapisuje do BQ (jeśli LOSE) lub tworzy 'ducha' (jeśli WIN)."""
    logger.info(f"--- [FINALIZACJA] --- [{trade.symbol}] | ID: {trade.trade_id} | Wynik: {closed_result}")
    
    if closed_result == "LOSE":
        logger.info(f"[{trade.trade_id}] Pozycja przegrana. Zapisuję bezpośrednio do BigQuery.")
        analytics_data = _calculate_rr_analytics(trade.entry_price, trade.sl_price, close_price, trade.direction)
        bq_data = {
            "trade_id": trade.trade_id, "timestamp_entry": trade.opened_at_iso,
            "timestamp_close": datetime.now(timezone.utc).isoformat(), "symbol": trade.symbol,
            "direction": trade.direction.upper(), "main_result": closed_result, "ob_type": trade.ob_type
        }
        bq_data.update(analytics_data)
        log_trade_to_bigquery(bq_data)
    elif closed_result == "WIN":
        logger.info(f"[{trade.trade_id}] Pozycja wygrana. Tworzę 'ducha' do dalszej analizy. BRAK zapisu do BQ w tym momencie.")
        state_manager.create_analyzed_trade(trade)


def _handle_setups(klines_data: Dict[str, Kline], active_setups: List[DocumentSnapshot]) -> List[Dict[str, Any]]:
    """Przetwarza aktywne setupy w poszukiwaniu wejść."""
    if not active_setups: return []
    logger.info(f"Sprawdzam {len(active_setups)} aktywnych setupów.")
    trades_to_finalize_immediately = []

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
                    trades_to_finalize_immediately.append({
                        "trade": fake_trade, 
                        "result": closed_result, 
                        "close_price": close_price
                    })
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
    
    return trades_to_finalize_immediately

#
# --- Wklej tę funkcję do bot_service/bot_logic.py, zastępując starą wersję ---
#

def _handle_manage_open_trades(klines_data: Dict[str, Kline], open_trades: List[DocumentSnapshot]) -> List[Any]:
    """
    Zarządza otwartymi pozycjami, sprawdza warunki zamknięcia i inicjuje proces finalizacji.
    Używa transakcji Firestore, aby zapewnić spójność danych.
    """
    tasks_to_run_async = []
    if not open_trades:
        return tasks_to_run_async

    logger.info(f"Zarządzam {len(open_trades)} otwartymi pozycjami.")

    for trade_doc in open_trades:
        trade_id = trade_doc.id
        # Główny blok try-except dla całego przetwarzania jednej pozycji, aby błąd w jednej nie zatrzymał pętli.
        try:
            trade = OpenTradeData.model_validate(trade_doc.to_dict())
            latest_kline = klines_data.get(trade.symbol)

            if not latest_kline:
                logger.warning(f"[{trade_id}] Brak danych kline z cache'u dla symbolu {trade.symbol}. Pomijam tę pozycję w cyklu.")
                continue

            closed_result, close_price = None, None
            if trade.direction == 'LONG':
                if latest_kline.low <= trade.sl_price:
                    closed_result, close_price = "LOSE", trade.sl_price
                elif latest_kline.high >= trade.tp_price:
                    closed_result, close_price = "WIN", trade.tp_price
            elif trade.direction == 'SHORT':
                if latest_kline.high >= trade.sl_price:
                    closed_result, close_price = "LOSE", trade.sl_price
                elif latest_kline.low <= trade.tp_price:
                    closed_result, close_price = "WIN", trade.tp_price

            if closed_result:
                logger.info(f"--- [DECYZJA: ZAMKNIĘCIE] --- [{trade.symbol}] | ID: {trade_id} | Wynik: {closed_result}")
                
                # Dodaj zadanie finalizacji (logowanie/tworzenie ducha) do wykonania asynchronicznego
                tasks_to_run_async.append(finalize_trade(trade, closed_result, close_price))
                
                # Użyj nowej, atomowej operacji do zmiany stanu w Firestore w ramach transakcji
                try:
                    db = get_db()
                    transaction = db.transaction()
                    state_manager.close_trade_transactional(
                        transaction,
                        trade.trade_id,
                        trade.symbol,
                        is_loss=(closed_result == "LOSE")
                    )
                    logger.info(f"[{trade_id}] Transakcja zamknięcia pozycji i resetu setupu zakończona pomyślnie.")
                except Exception as ex:
                    # Ten błąd jest krytyczny, ponieważ stan w Firestore może być niespójny.
                    logger.critical(f"[{trade_id}] KRYTYCZNY BŁĄD TRANSAKCJI ZAMKNIĘCIA: {ex}", exc_info=True)

        except ValidationError as e:
            logger.error(f"[{trade_id}] Błąd walidacji danych otwartej pozycji: {e}")
        except Exception as e:
            logger.error(f"[{trade_id}] Nieoczekiwany błąd podczas monitorowania otwartej pozycji: {e}", exc_info=True)

    return tasks_to_run_async

#
# --- Wklej tę funkcję do bot_service/bot_logic.py, ZASTĘPUJĄC `_handle_post_mortem_analysis_optimized` ---
#

def _handle_post_mortem_analysis(analyzed_trades: List[DocumentSnapshot], klines_data: Dict[str, Kline]):
    """
    Analizuje 'duchy' w sposób WYDAJNY, korzystając WYŁĄCZNIE z danych z cache'u klines.
    Zapis do BQ odbywa się JEDEN RAZ na końcu życia 'ducha'.
    """
    if not analyzed_trades:
        return
    logger.info(f"[ANALIZA DUCHA] Rozpoczynam analizę dla {len(analyzed_trades)} 'duchów' w oparciu o cache.")

    for trade_doc in analyzed_trades:
        trade_id = trade_doc.id
        try:
            analysis_trade = AnalyzedTradeData.model_validate(trade_doc.to_dict())
            symbol = analysis_trade.symbol
            
            latest_kline = klines_data.get(symbol)
            if not latest_kline:
                logger.warning(f"[ANALIZA DUCHA][{trade_id}] Brak danych z cache'u kline dla symbolu {symbol}. Pomijam.")
                continue

            # Krok 1: Aktualizuj stan 'ducha' o nowe ekstremum z ostatniej świecy
            current_extreme = analysis_trade.last_known_extreme_price
            new_extreme = current_extreme
            if analysis_trade.direction == 'LONG' and latest_kline.high > current_extreme:
                new_extreme = latest_kline.high
            elif analysis_trade.direction == 'SHORT' and latest_kline.low < current_extreme:
                new_extreme = latest_kline.low
            
            if new_extreme != current_extreme:
                state_manager.update_analyzed_trade_state(trade_id, new_extreme, latest_kline.timestamp)
                analysis_trade.last_known_extreme_price = new_extreme
            
            # Krok 2: Sprawdź warunki końca analizy
            is_analysis_finished, reason = False, ""
            if analysis_trade.direction == 'LONG':
                if latest_kline.low <= analysis_trade.original_sl:
                    is_analysis_finished, reason = True, "osiągnięto SL"
                elif analysis_trade.original_tp_5_0 and latest_kline.high >= analysis_trade.original_tp_5_0:
                    is_analysis_finished, reason = True, "osiągnięto TP5"
            else: # SHORT
                if latest_kline.high >= analysis_trade.original_sl:
                    is_analysis_finished, reason = True, "osiągnięto SL"
                elif analysis_trade.original_tp_5_0 and latest_kline.low <= analysis_trade.original_tp_5_0:
                    is_analysis_finished, reason = True, "osiągnięto TP5"

            # Krok 3: Sfinalizuj, jeśli analiza zakończona
            if is_analysis_finished:
                logger.info(f"[ANALIZA DUCHA][{trade_id}] Warunek końca spełniony ({reason}). Zapisuję ostateczny wynik do BQ.")
                
                final_analytics = _calculate_rr_analytics(
                    analysis_trade.entry_price,
                    analysis_trade.original_sl,
                    analysis_trade.last_known_extreme_price,
                    analysis_trade.direction
                )
                
                bq_data = {
                    "trade_id": analysis_trade.trade_id,
                    "timestamp_entry": datetime.fromtimestamp(analysis_trade.opened_at_ms / 1000, tz=timezone.utc).isoformat(),
                    "timestamp_close": datetime.now(timezone.utc).isoformat(),
                    "symbol": analysis_trade.symbol,
                    "direction": analysis_trade.direction.upper(),
                    "main_result": "WIN",
                    "ob_type": analysis_trade.alert_data_snapshot.get('ob_type', 'Used OB')
                }
                bq_data.update(final_analytics)
                
                # Zapisujemy PIERWSZY i OSTATNI raz do BigQuery
                log_trade_to_bigquery(bq_data)
                state_manager.remove_analyzed_trade(trade_id)

        except Exception as e: 
            logger.error(f"[ANALIZA DUCHA][{trade_id}] Błąd podczas analizy post-mortem: {e}", exc_info=True)


#
# --- Wklej tę funkcję do bot_service/bot_logic.py, ZASTĘPUJĄC `run_trading_logic` ---
#

def run_trading_logic():
    logger.info("Rozpoczynam główną pętlę logiki tradingowej.")

    # Krok 1: Zbierz wszystkie symbole, które musimy monitorować
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

    # Krok 2: Pobierz wszystkie potrzebne dane z cache'u JEDEN RAZ na początku cyklu
    klines_data_from_cache = state_manager.get_latest_klines_from_cache(list(valid_symbols))
    if not klines_data_from_cache:
        logger.warning("Nie udało się pobrać danych z cache'u klines. Nie można kontynuować cyklu.")
        return

    klines_data = {
        symbol: Kline.model_validate(data) for symbol, data in klines_data_from_cache.items()
    }
    
    active_setups_docs = list(state_manager.get_all_active_setups())

    # --- SEKWENCJA OPERACJI (w pełni synchroniczna logika, asynchroniczna finalizacja) ---

    # Krok 3: Zarządzaj 'duchami' na podstawie danych z cache'u
    _handle_post_mortem_analysis(analyzed_trades_docs, klines_data)
    
    # Krok 4: Szukaj nowych wejść
    immediate_finalization_tasks = _handle_setups(klines_data, active_setups_docs)

    # Krok 5: Zarządzaj otwartymi pozycjami
    closing_tasks = _handle_manage_open_trades(klines_data, open_trades_docs)

    # Krok 6: Zbierz wszystkie zadania do asynchronicznej finalizacji i uruchom je
    all_async_tasks = [job['task'] for job in immediate_finalization_tasks] + closing_tasks
    if all_async_tasks:
        logger.info(f"Uruchamiam {len(all_async_tasks)} zadań finalizacji (logowanie/tworzenie duchów).")
        try:
            asyncio.run(asyncio.gather(*all_async_tasks, return_exceptions=True))
        except Exception as e:
            logger.error(f"Błąd podczas uruchamiania pętli asyncio dla finalizacji: {e}", exc_info=True)

    logger.info("Zakończono główną pętlę logiki tradingowej.")