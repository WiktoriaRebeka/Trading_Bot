# bot_service/bot_logic.py

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from google.cloud.firestore_v1.document import DocumentSnapshot
from pydantic import ValidationError
from requests.exceptions import RequestException

from shared_lib import constants
from shared_lib.firebase_client import get_db, get_symbols_to_watch_from_config
from shared_lib.leverage_calculator import get_all_calculations_for_alert, format_quantity
from shared_lib.models import (
    AlertData,
    AnalyzedTradeData,
    Kline,
    OpenTradeData,
    SetupData,
)

from bot_service import state_manager
from bot_service.bigquery_logger import log_trade_to_bigquery
from bot_service.bybit_executor import (
    BybitAPIError,
    BybitExecutor,
)

logger = logging.getLogger(__name__)

# ==============================================================================
# === NOWA, SKONSOLIDOWANA FUNKCJA REALIZUJĄCA ZADANIE ===
# ==============================================================================

def process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]], bybit_executor: BybitExecutor):
    """
    Przetwarza nowe alerty i natychmiast próbuje złożyć na ich podstawie
    zlecenia na giełdzie Bybit.
    Ta funkcja NIE tworzy żadnych zapisów w lokalnej bazie danych (np. open_trades).
    Jej rola jest czysto wykonawcza.
    """
    if not newly_fetched_alerts:
        return
    logger.info(f"Rozpoczynam przetwarzanie {len(newly_fetched_alerts)} nowych alertów w celu złożenia zleceň.")
    
    for alert_dict in newly_fetched_alerts:
        alert_id = alert_dict.get('id', 'N/A')
        symbol = "N/A"  # Domyślna wartość na wypadek błędu walidacji
        try:
            alert_data = AlertData.model_validate(alert_dict)
            symbol = alert_data.symbol
            
            logger.info(f"--- [{symbol}][Alert: {alert_id}] Rozpoczynam przetwarzanie zlecenia ---")

            # Krok 1: Obliczenie wymaganej dźwigni za pomocą współdzielonego modułu
            leverage_calcs = get_all_calculations_for_alert(alert_data)
            required_leverage = leverage_calcs.get('required_leverage')

            if not required_leverage: # Sprawdzamy czy nie jest None lub 0
                logger.warning(f"[{symbol}] Zlecenie odrzucone. Wymagana dźwignia nie mogła zostać obliczona lub jest < 1. Kończę przetwarzanie tego alertu.")
                continue

            # Krok 2: Weryfikacja maksymalnej dźwigni na giełdzie
            instrument_info = bybit_executor.get_instrument_info(symbol)
            if not instrument_info:
                logger.error(f"[{symbol}] Nie udało się pobrać informacji o instrumencie z Bybit. Nie można złożyć zlecenia.")
                continue
            
            max_leverage_from_api = instrument_info.get('max_leverage', 1.0)
            final_leverage = min(required_leverage, max_leverage_from_api)
            logger.info(f"[{symbol}] Dźwignia: Wymagana={required_leverage}x, Max giełdy={max_leverage_from_api}x. Wybrano: {final_leverage}x.")
            
            # Krok 3: Przygotowanie i złożenie zlecenia
            order_params = {
                "symbol": symbol,
                "side": alert_data.direction,
                "price": alert_data.entry,
                "qty": "10",  # Stała wartość 10 USDT
                "leverage": final_leverage,
                "takeProfit": alert_data.tp_2_0,
                "stopLoss": alert_data.sl
            }
            order_id = bybit_executor.place_limit_order(order_params)

            # Krok 4: Logowanie wyniku operacji
            if order_id:
                logger.info(f"[{symbol}][Alert: {alert_id}] SUKCES. Zlecenie zostało pomyślnie wysłane do Bybit. Order ID: {order_id}")
            else:
                logger.error(f"[{symbol}][Alert: {alert_id}] PORAŻKA. Nie udało się złożyć zlecenia na giełdzie.")

        except ValidationError as e:
            logger.error(f"Błąd walidacji danych alertu {alert_id}: {e}", extra={"json_fields": {"alert_id": alert_id}})
        except (BybitAPIError, RequestException) as e:
            # Błąd jest już logowany w executorze, tutaj dodajemy kontekst alertu
            logger.critical(f"[{symbol}] Błąd API Bybit podczas przetwarzania alertu {alert_id}.")
        except Exception as e:
            logger.critical(f"[{symbol}] Nieoczekiwany błąd w logice przetwarzania alertu {alert_id}: {e}", exc_info=True)


# ==============================================================================
# === ISTNIEJĄCA LOGIKA BIZNESOWA, KTÓRA POZOSTAJE BEZ ZMIAN ===
# ==============================================================================

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

def finalize_trade(trade: OpenTradeData, closed_result: str, close_price: float):
    logger.info(f"--- [FINALIZACJA] --- [{trade.symbol}] | ID: {trade.trade_id} | Wynik: {closed_result}")
    if closed_result == "LOSE":
        logger.info(f"[{trade.trade_id}] Pozycja przegrana. Analiza historyczna i zapis do BigQuery.")
        end_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        historical_klines = state_manager.get_historical_klines(trade.symbol, trade.opened_at_ms, end_time_ms)
        extreme_price = trade.entry_price
        if historical_klines:
            if trade.direction == 'LONG':
                extreme_price = max(k['high'] for k in historical_klines)
            elif trade.direction == 'SHORT':
                extreme_price = min(k['low'] for k in historical_klines)
        analytics_data = _calculate_rr_analytics(trade.entry_price, trade.sl_price, extreme_price, trade.direction)
        bq_data = {
            "trade_id": trade.trade_id, 
            "timestamp_entry": trade.opened_at_iso,
            "timestamp_close": datetime.now(timezone.utc).isoformat(), 
            "symbol": trade.symbol,
            "direction": trade.direction.upper(), 
            "main_result": "LOSE", 
            "ob_type": trade.ob_type
        }
        bq_data.update(analytics_data)
        log_trade_to_bigquery(bq_data)
    elif closed_result == "WIN":
        logger.info(f"[{trade.trade_id}] Pozycja wygrana. Tworzę 'ducha' do dalszej analizy.")
        state_manager.create_analyzed_trade(trade)

def _handle_setups(klines_data: Dict[str, Kline], active_setups: List[DocumentSnapshot], bybit_executor: BybitExecutor):
    if not active_setups: return
    logger.info(f"Sprawdzam {len(active_setups)} aktywnych setupów.")
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
                    logger.info(f"[{symbol}] Warunek resetu ceny spełniony. Setup gotowy do nowego wejścia.")
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
                
                if closed_result:
                    trade_id = str(uuid.uuid4())
                    logger.info(f"--- [WEJŚCIE I ZAMKNIĘCIE W 1 MIN] --- [{symbol}] | Wynik: {closed_result} | ID: {trade_id}")
                    entry_timestamp = datetime.fromtimestamp(latest_kline.timestamp / 1000, tz=timezone.utc)
                    fake_trade = OpenTradeData(
                        trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
                        entry_price=entry_level, sl_price=sl_price, tp_price=tp_price,
                        opened_at_ms=latest_kline.timestamp, opened_at_iso=entry_timestamp.isoformat(),
                        alert_data_snapshot=setup.alert_data.model_dump(by_alias=True),
                        bybit_order_id="immediate_close_no_order"
                    )
                    finalize_trade(fake_trade, closed_result, close_price)
                    try:
                        db = get_db()
                        transaction = db.transaction()
                        state_manager.update_setup_after_immediate_close_transactional(transaction, symbol, is_loss=(closed_result == "LOSE"))
                    except Exception as ex:
                        logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD TRANSAKCJI: {ex}", exc_info=True)
                else:
                    logger.info(f"--- [DECYZJA: WEJŚCIE {ob_type}] --- [{symbol}] | Cena: {entry_level} | Rozpoczynam proces składania zlecenia.")
                    
                    leverage_calcs = get_all_calculations_for_alert(setup.alert_data)
                    required_leverage = leverage_calcs.get('required_leverage')

                    if not required_leverage or required_leverage < 1:
                        logger.warning(f"[{symbol}] Zlecenie odrzucone. Wymagana dźwignia ({required_leverage}) jest nieprawidłowa.")
                        continue

                    try:
                        instrument_info = bybit_executor.get_instrument_info(symbol)
                        if not instrument_info:
                            logger.error(f"[{symbol}] Nie udało się pobrać informacji o instrumencie. Przerywam.")
                            continue
                        
                        max_leverage = instrument_info['max_leverage']
                        qty_step = instrument_info['qty_step']
                        min_order_qty = instrument_info['min_order_qty']
                        final_leverage = min(required_leverage, max_leverage)
                        
                        target_qty = 10.0 / entry_level
                        
                        if target_qty < min_order_qty:
                            logger.warning(f"[{symbol}] Docelowa ilość ({target_qty:.6f}) jest mniejsza niż minimum giełdowe ({min_order_qty}). Używam minimalnej ilości.")
                            final_qty = min_order_qty
                        else:
                            final_qty = target_qty
                        
                        formatted_qty = format_quantity(final_qty, qty_step)

                        if float(formatted_qty) <= 0:
                            logger.error(f"[{symbol}] Obliczona wielkość zlecenia po sformatowaniu ({formatted_qty}) jest zerowa. Przerywam.")
                            continue

                        order_params = {
                            "symbol": symbol, "side": direction, "price": str(entry_level),
                            "qty": formatted_qty, "leverage": str(final_leverage),
                            "takeProfit": str(setup.alert_data.tp_2_0), "stopLoss": str(sl_price)
                        }
                        order_id = bybit_executor.place_limit_order(order_params)

                        if order_id:
                            trade_id = str(uuid.uuid4())
                            logger.info(f"[{symbol}] Zlecenie pomyślnie wysłane. Tworzę dokument w open_trades z trade_id: {trade_id} i bybit_order_id: {order_id}")
                            state_manager.create_open_trade(
                                trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
                                entry_price=entry_level, sl_price=sl_price, tp_price=tp_price,
                                alert_data=setup.alert_data, bybit_order_id=order_id
                            )
                        else:
                            logger.error(f"[{symbol}] Nie udało się uzyskać ID zlecenia od Bybit.")

                    except (BybitAPIError, RequestException) as e:
                        logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD podczas interakcji z API Bybit: {e}")
                    except Exception as e:
                        logger.critical(f"[{symbol}] Nieoczekiwany błąd w logice otwierania pozycji: {e}", exc_info=True)
        except ValidationError as e:
            logger.error(f"Błąd walidacji danych setupu dla {symbol}: {e}")
        except Exception as e:
            logger.error(f"Błąd podczas sprawdzania wejścia dla {symbol}: {e}", exc_info=True)
           
def _handle_manage_open_trades(klines_data: Dict[str, Kline], open_trades: List[DocumentSnapshot]):
    if not open_trades: return
    logger.info(f"Zarządzam {len(open_trades)} otwartymi pozycjami.")
    for trade_doc in open_trades:
        trade_id = trade_doc.id
        try:
            trade = OpenTradeData.model_validate(trade_doc.to_dict())
            latest_kline = klines_data.get(trade.symbol)
            if not latest_kline:
                logger.warning(f"[{trade_id}] Brak danych kline dla {trade.symbol}. Pomijam.")
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
                    logger.info(f"[{trade_id}] Transakcja zamknięcia pozycji zakończona.")
                except Exception as ex:
                    logger.critical(f"[{trade_id}] KRYTYCZNY BŁĄD TRANSAKCJI ZAMKNIĘCIA: {ex}", exc_info=True)
        except ValidationError as e:
            logger.error(f"Błąd walidacji danych otwartej pozycji {trade_id}: {e}")
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd podczas monitorowania pozycji {trade_id}: {e}", exc_info=True)

def _handle_post_mortem_analysis(analyzed_trades: List[DocumentSnapshot], klines_data: Dict[str, Kline]):
    if not analyzed_trades:
        return
    logger.info(f"[ANALIZA DUCHA] Rozpoczynam analizę dla {len(analyzed_trades)} 'duchów'.")
    for trade_doc in analyzed_trades:
        trade_id = trade_doc.id
        try:
            analysis_trade = AnalyzedTradeData.model_validate(trade_doc.to_dict())
            symbol = analysis_trade.symbol
            latest_kline = klines_data.get(symbol)
            if not latest_kline:
                continue

            current_extreme = analysis_trade.last_known_extreme_price
            new_extreme = current_extreme
            if analysis_trade.direction == 'LONG' and latest_kline.high > current_extreme:
                new_extreme = latest_kline.high
            elif analysis_trade.direction == 'SHORT' and latest_kline.low < current_extreme:
                new_extreme = latest_kline.low
            
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
                logger.info(f"[ANALIZA DUCHA][{trade_id}] ZAKOŃCZONO ANALIZĘ ({reason}). Zapisuję finalny rekord do BQ.")
                final_analytics = _calculate_rr_analytics(
                    analysis_trade.entry_price, 
                    analysis_trade.original_sl, 
                    analysis_trade.last_known_extreme_price, 
                    analysis_trade.direction
                )
                final_bq_data = {
                    "trade_id": trade_id,
                    "timestamp_entry": datetime.fromtimestamp(analysis_trade.opened_at_ms / 1000, tz=timezone.utc).isoformat(),
                    "timestamp_close": datetime.now(timezone.utc).isoformat(),
                    "symbol": analysis_trade.symbol,
                    "direction": analysis_trade.direction,
                    "main_result": "WIN",
                    "ob_type": analysis_trade.ob_type 
                }
                final_bq_data.update(final_analytics)
                log_trade_to_bigquery(final_bq_data)
                state_manager.remove_analyzed_trade(trade_id)
        except Exception as e: 
            logger.error(f"[ANALIZA DUCHA][{trade_id}] Błąd: {e}", exc_info=True)

def run_trading_logic(bybit_executor: BybitExecutor):
    logger.info("Rozpoczynam główną pętlę logiki (tryb: tylko monitorowanie).")
    
    symbols_to_watch = set(get_symbols_to_watch_from_config())
    # UWAGA: Poniższe funkcje `_handle_setups`, `_handle_manage_open_trades` i `_handle_post_mortem_analysis`
    # są teraz nieaktywne, ponieważ rezygnujemy z lokalnego zarządzania stanem pozycji.
    # W przyszłości można je usunąć lub zostawić jako referencję.
    # Na ten moment, kod pozostaje, aby nie naruszać zasady o niemodyfikowaniu istniejącej logiki.
    active_setups = list(state_manager.get_all_active_setups())
    open_trades_docs = list(state_manager.get_all_open_trades())
    analyzed_trades_docs = list(state_manager.get_all_analyzed_trades())

    for doc in active_setups:
        if data := doc.to_dict(): symbols_to_watch.add(data.get('alert_data', {}).get('symbol'))
    for doc in open_trades_docs:
        if data := doc.to_dict(): symbols_to_watch.add(data.get('symbol'))
    for doc in analyzed_trades_docs:
        if data := doc.to_dict(): symbols_to_watch.add(data.get('symbol'))
        
    valid_symbols = {s for s in symbols_to_watch if isinstance(s, str) and s}
    if not valid_symbols:
        logger.info("Brak symboli do monitorowania. Kończę cykl.")
        return
        
    klines_data_from_cache = state_manager.get_latest_klines_from_cache(list(valid_symbols))
    if not klines_data_from_cache:
        logger.warning("Nie udało się pobrać danych z cache'u klines.")
        return
        
    klines_data = {
        symbol: Kline.model_validate(data) for symbol, data in klines_data_from_cache.items()
    }
    
    _handle_setups(klines_data, active_setups, bybit_executor)
    _handle_post_mortem_analysis(analyzed_trades_docs, klines_data)
    _handle_manage_open_trades(klines_data, open_trades_docs)
    
    logger.info("Zakończono główną pętlę logiki.")