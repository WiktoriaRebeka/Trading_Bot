# Lokalizacja: bot_service/bot_logic.py
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
from shared_lib.models import SetupData, OpenTradeData, AnalyzedTradeData, AlertData, Kline

logger = logging.getLogger(__name__)

# --- ULEPSZONE FUNKCJE ---

# Lokalizacja: bot_service/bot_logic.py

def _calculate_rr_analytics(entry_price: float, sl_price: float, extreme_price: float) -> Dict[str, Any]:
    """Kalkuluje osiągnięte R:R i flagi dla poszczególnych poziomów TP."""
    risk_diff = abs(entry_price - sl_price)
    if risk_diff == 0:
        logger.warning(f"Różnica ryzyka wynosi zero (entry={entry_price}, sl={sl_price}). R:R ustawione na 0.")
        return {"rr_achieved": 0.0}

    # --- KLUCZOWA POPRAWKA LOGICZNA ---
    # Sprawdzamy, czy kierunek ruchu ceny jest zgodny z kierunkiem transakcji.
    # Jeśli nie, zysk wynosi 0.
    is_long = sl_price < entry_price
    
    if is_long and extreme_price < entry_price:
        profit_diff = 0.0 # Cena poszła w dół dla longa
    elif not is_long and extreme_price > entry_price:
        profit_diff = 0.0 # Cena poszła w górę dla shorta
    else:
        profit_diff = abs(extreme_price - entry_price)
    # ------------------------------------

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
            # Używamy modelu AlertData do walidacji i ustawienia kierunku
            alert_data = AlertData.model_validate(alert_dict)
            
            # Tworzymy nowy obiekt SetupData
            new_setup = SetupData(
                alert_data=alert_data,
                updated_at=datetime.now(timezone.utc)
            )
            
            # Zapisujemy/nadpisujemy dokument w kolekcji active_setups
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
                # Walidacja i konwersja na obiekty Kline
                klines = [
                    Kline(timestamp=int(k[0]), high=float(k[2]), low=float(k[3]), close=float(k[4]))
                    for k in reversed(data["result"]["list"])
                ]
                return klines
    except Exception as e:
        logger.error(f"[{symbol}] Błąd przy pobieraniu historii kline: {e}", exc_info=True)
    return []

async def log_initial_trade_result(trade: OpenTradeData, closed_result: str, close_price: float):
    """Loguje początkowy wynik transakcji do BigQuery."""
    logger.info(f"--- [FINALIZACJA] --- [{trade.symbol}] | ID: {trade.trade_id} | Wynik: {closed_result}")
    
    analytics_data = _calculate_rr_analytics(trade.entry_price, trade.sl_price, close_price)
    
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
    log_trade_to_bigquery(bq_data)

    if closed_result == "WIN":
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
            if setup.is_position_open_on_this_setup: continue
            
            latest_kline = klines_data.get(symbol)
            if not latest_kline: continue

            direction = setup.alert_data.direction
            entry_level = setup.alert_data.entry
            sl_price = setup.alert_data.sl
            tp_price = setup.alert_data.tp

            if setup.is_reset_needed_after_loss:
                if (direction == 'LONG' and latest_kline.high > entry_level) or \
                   (direction == 'SHORT' and latest_kline.low < entry_level):
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
                    # Używamy timestampu świecy, która wywołała zdarzenie
                    entry_timestamp = datetime.fromtimestamp(latest_kline.timestamp / 1000, tz=timezone.utc)
                    fake_trade = OpenTradeData(
                        trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
                        entry_price=entry_level, sl_price=sl_price, tp_price=tp_price,
                        opened_at_ms=latest_kline.timestamp, opened_at_iso=entry_timestamp.isoformat(),
                        alert_data_snapshot=setup.alert_data.model_dump(by_alias=True)
                    )
                    trades_to_finalize_immediately.append({"trade": fake_trade, "result": closed_result, "close_price": close_price})
                    state_manager.update_setup_after_trade_close(symbol, is_loss=(closed_result == "LOSE"))
                    state_manager.update_setup_entry_attempt(symbol)
                else:
                    logger.info(f"--- [DECYZJA: WEJŚCIE {ob_type}] --- [{symbol}] | Cena: {entry_level} | ID: {trade_id}")
                    state_manager.create_open_trade(
                        trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
                        entry_price=entry_level, sl_price=sl_price, tp_price=tp_price,
                        alert_data=setup.alert_data
                    )
        except ValidationError as e: logger.error(f"[{symbol}] Błąd walidacji danych setupu: {e}")
        except Exception as e: logger.error(f"[{symbol}] Błąd podczas sprawdzania wejścia: {e}", exc_info=True)
    
    return trades_to_finalize_immediately

async def _handle_manage_open_trades(klines_data: Dict[str, Kline], open_trades: List[DocumentSnapshot]):
    """Monitoruje otwarte pozycje i zamyka je w razie potrzeby."""
    if not open_trades: return
    logger.info(f"Monitoruję {len(open_trades)} otwartych pozycji.")
    tasks_to_run = []

    for trade_doc in open_trades:
        trade_id = trade_doc.id
        try:
            trade = OpenTradeData.model_validate(trade_doc.to_dict())
            latest_kline = klines_data.get(trade.symbol)
            if not latest_kline: continue

            closed_result, close_price = None, None
            if trade.direction == 'LONG':
                if latest_kline.low <= trade.sl_price: closed_result, close_price = "LOSE", trade.sl_price
                elif latest_kline.high >= trade.tp_price: closed_result, close_price = "WIN", trade.tp_price
            elif trade.direction == 'SHORT':
                if latest_kline.high >= trade.sl_price: closed_result, close_price = "LOSE", trade.sl_price
                elif latest_kline.low <= trade.tp_price: closed_result, close_price = "WIN", trade.tp_price
            
            if closed_result:
                tasks_to_run.append(log_initial_trade_result(trade, closed_result, close_price))
                state_manager.remove_open_trade(trade_id)
                state_manager.update_setup_after_trade_close(trade.symbol, is_loss=(closed_result == "LOSE"))
        
        except ValidationError as e: logger.error(f"[{trade_id}] Błąd walidacji danych otwartej pozycji: {e}")
        except Exception as e: logger.error(f"[{trade_id}] Błąd podczas monitorowania otwartej pozycji: {e}", exc_info=True)
    
    if tasks_to_run: await asyncio.gather(*tasks_to_run)

async def _handle_post_mortem_analysis_optimized(session: aiohttp.ClientSession, klines_data: Dict[str, Kline], analyzed_trades: List[DocumentSnapshot]):
    """
    ZOPTYMALIZOWANA analiza post-mortem. Pobiera tylko nowe świece i inkrementalnie
    aktualizuje stan w Firestore i BigQuery.
    """
    if not analyzed_trades: return
    logger.info(f"Analizuję {len(analyzed_trades)} 'duchów' (post-mortem).")

    for trade_doc in analyzed_trades:
        trade_id = trade_doc.id
        try:
            analysis_trade = AnalyzedTradeData.model_validate(trade_doc.to_dict())
            symbol = analysis_trade.symbol
            latest_kline = klines_data.get(symbol)
            if not latest_kline: continue

            # 1. Sprawdzenie warunków zakończenia analizy
            is_analysis_finished, reason = False, ""
            if analysis_trade.direction == 'LONG':
                if latest_kline.low <= analysis_trade.original_sl: is_analysis_finished, reason = True, "osiągnięto SL"
                elif analysis_trade.original_tp_5_0 and latest_kline.high >= analysis_trade.original_tp_5_0: is_analysis_finished, reason = True, "osiągnięto TP5"
            else: # SHORT
                if latest_kline.high >= analysis_trade.original_sl: is_analysis_finished, reason = True, "osiągnięto SL"
                elif analysis_trade.original_tp_5_0 and latest_kline.low <= analysis_trade.original_tp_5_0: is_analysis_finished, reason = True, "osiągnięto TP5"

            if is_analysis_finished:
                logger.info(f"[{trade_id}] Analiza 'ducha' zakończona ({reason}). Finalny UPDATE i usunięcie.")
                # Ostatnia aktualizacja BQ z ceną, która zakończyła analizę
                final_extreme_price = latest_kline.high if analysis_trade.direction == 'LONG' else latest_kline.low
                updates_for_bq = _calculate_rr_analytics(analysis_trade.entry_price, analysis_trade.original_sl, final_extreme_price)
                update_analyzed_trade_in_bigquery(trade_id, updates_for_bq)
                state_manager.remove_analyzed_trade(trade_id)
                continue

            # 2. Inkrementalne pobieranie nowych świec
            new_klines = await get_historical_klines(session, symbol, analysis_trade.last_analysis_timestamp_ms + 1)
            if not new_klines: continue

            # 3. Znalezienie nowej ceny ekstremalnej
            current_extreme = analysis_trade.last_known_extreme_price
            if analysis_trade.direction == 'LONG':
                new_extreme = max(k.high for k in new_klines)
                has_new_extreme = new_extreme > current_extreme
            else: # SHORT
                new_extreme = min(k.low for k in new_klines)
                has_new_extreme = new_extreme < current_extreme

            # 4. Aktualizacja stanu, jeśli jest postęp
            if has_new_extreme:
                logger.info(f"[{trade_id}] 'Duch' osiągnął nowy potencjał. Cena: {new_extreme}. Aktualizuję BQ.")
                updates_for_bq = _calculate_rr_analytics(analysis_trade.entry_price, analysis_trade.original_sl, new_extreme)
                
                # Filtrujemy flagi, które są nowe
                newly_achieved_tps = {k for k, v in updates_for_bq.items() if v and k.startswith('rr_')}
                already_achieved = set(analysis_trade.achieved_tps)
                
                if newly_achieved_tps - already_achieved:
                    update_analyzed_trade_in_bigquery(trade_id, updates_for_bq)
                    state_manager.update_analyzed_trade_state(
                        trade_id, new_extreme, new_klines[-1].timestamp, list(newly_achieved_tps)
                    )
            else:
                # Aktualizujemy tylko timestamp, żeby nie pobierać tych samych świec ponownie
                state_manager.update_analyzed_trade_state(
                    trade_id, current_extreme, new_klines[-1].timestamp, analysis_trade.achieved_tps
                )

        except ValidationError as e: logger.error(f"[{trade_id}] Błąd walidacji danych 'ducha', pomijam: {e}")
        except Exception as e: logger.error(f"[{trade_id}] Błąd podczas analizy post-mortem: {e}", exc_info=True)

def run_trading_logic():
    logger.info("Rozpoczynam główną pętlę logiki tradingowej.")
    
    # ZMIANA: Pobieramy listę symboli z konfiguracji, a nie przez skanowanie kolekcji
    symbols_to_watch = set(state_manager.get_symbols_to_watch_from_config())
    
    # Dodajemy symbole z otwartych pozycji i duchów, które mogły zniknąć z głównej listy
    for doc in state_manager.get_all_open_trades():
        if data := doc.to_dict(): symbols_to_watch.add(data.get('symbol'))
    for doc in state_manager.get_all_analyzed_trades():
        if data := doc.to_dict(): symbols_to_watch.add(data.get('symbol'))

    valid_symbols_to_watch = {s for s in symbols_to_watch if isinstance(s, str) and s}
    if not valid_symbols_to_watch:
        logger.info("Brak poprawnych symboli do monitorowania. Kończę cykl.")
        return

    latest_klines_cache = state_manager.get_latest_klines_from_cache(list(valid_symbols_to_watch))
    if not latest_klines_cache:
        logger.warning("Nie udało się pobrać danych z cache'u klines. Nie można kontynuować.")
        return
    
    # ZMIANA: Używamy modelu Pydantic zamiast "magicznej" listy
    klines_data_for_handlers: Dict[str, Kline] = {}
    for symbol, data in latest_klines_cache.items():
        try:
            klines_data_for_handlers[symbol] = Kline(
                timestamp=data['kline_timestamp'], high=data['high'], low=data['low'], close=data['close']
            )
        except (KeyError, TypeError) as e:
            logger.warning(f"[{symbol}] Brakujące lub nieprawidłowe dane w cache'u klines: {e}")

    all_setups = list(state_manager.get_all_active_setups())
    all_open_trades = list(state_manager.get_all_open_trades())
    all_analyzed_trades = list(state_manager.get_all_analyzed_trades())

    async def async_main():
        async with aiohttp.ClientSession() as session:
            # Przetwarzanie setupów jest synchroniczne, ale zwraca zadania do asynchronicznej finalizacji
            immediate_finalization_jobs = _handle_setups(klines_data_for_handlers, all_setups)
            
            tasks = [
                log_initial_trade_result(job["trade"], job["result"], job["close_price"])
                for job in immediate_finalization_jobs
            ]
            tasks.append(_handle_manage_open_trades(klines_data_for_handlers, all_open_trades))
            tasks.append(_handle_post_mortem_analysis_optimized(session, klines_data_for_handlers, all_analyzed_trades))
            
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
