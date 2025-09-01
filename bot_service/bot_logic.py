# Lokalizacja: bot_service/bot_logic.py

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple

from google.cloud.firestore_v1.document import DocumentSnapshot
from pydantic import ValidationError
from requests.exceptions import RequestException

from shared_lib import constants
from shared_lib.firebase_client import get_db, get_symbols_to_watch_from_config
from shared_lib.leverage_calculator import get_all_calculations_for_alert, format_price
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
    format_quantity,
)

logger = logging.getLogger(__name__)

def _prepare_and_place_order(alert_data: AlertData, bybit_executor: BybitExecutor, alert_id: str = 'N/A') -> Tuple[Optional[str], Optional[Decimal]]:
    """
    Przygotowuje i składa zlecenie, implementując precyzyjną strategię zarządzania ryzykiem.
    - Maksymalna Strata (Ryzyko): 2.50 USDT
    - Minimalny Zysk (Nagroda): 5.00 USDT
    Zwraca krotkę (order_id, take_profit_price) w przypadku sukcesu lub (None, None) w przypadku porażki.
    """
    symbol = alert_data.symbol
    logger.info(f"[{symbol}] --- Rozpoczynam kalkulację ryzyka dla alertu {alert_id} ---")

    try:
        # === KROK 1: POBRANIE DANYCH ===
        # Usunięto stąd sprawdzanie has_open_position, ponieważ jest ono teraz w logice nadrzędnej.
        
        instrument_info = bybit_executor.get_instrument_info(symbol)
        if not instrument_info:
            logger.error(f"[{symbol}] Nie udało się pobrać informacji o instrumencie. Przerywam.")
            return None, None

        # ... reszta funkcji pozostaje bez zmian ...
        tick_size = Decimal(instrument_info.get('tick_size'))
        qty_step = Decimal(instrument_info.get('qty_step'))
        min_order_qty = Decimal(instrument_info.get('min_order_qty'))
        
        entry_price = Decimal(str(alert_data.entry))
        sl_price = Decimal(str(alert_data.sl))

        TARGET_RISK_USDT = Decimal("2.50")
        TARGET_REWARD_USDT = Decimal("5.00")
        TAKER_FEE_RATE = Decimal("0.00055")
        MIN_SL_DISTANCE_PERCENT = Decimal("0.0005")

        # === KROK 2: WALIDACJA I OBLICZENIE STRATY ===

        if entry_price <= 0:
            logger.error(f"[{symbol}] Cena wejścia jest nieprawidłowa: {entry_price}. Przerywam.")
            return None, None
            
        sl_distance_percentage = abs(entry_price - sl_price) / entry_price
        if sl_distance_percentage == 0:
            logger.error(f"[{symbol}] Odległość SL wynosi zero. Przerywam.")
            return None, None

        if sl_distance_percentage < MIN_SL_DISTANCE_PERCENT:
            logger.warning(
                f"[{symbol}] Zlecenie odrzucone. Odległość SL ({sl_distance_percentage:.4%}) "
                f"jest mniejsza niż wymagane minimum ({MIN_SL_DISTANCE_PERCENT:.4%})."
            )
            return None, None

        total_cost_percentage = sl_distance_percentage + (TAKER_FEE_RATE * 2)

        # === KROK 3: DYNAMICZNE OBLICZANIE WIELKOŚCI POZYCJI ===

        notional_value = TARGET_RISK_USDT / total_cost_percentage
        target_qty = notional_value / entry_price
        
        if target_qty < min_order_qty:
            logger.warning(f"[{symbol}] Zlecenie odrzucone. Obliczona ilość ({target_qty}) < minimum giełdowe ({min_order_qty}).")
            return None, None
        
        formatted_qty = target_qty.quantize(qty_step, rounding=ROUND_DOWN)
        
        if formatted_qty <= 0:
            logger.error(f"[{symbol}] Po zaokrągleniu ilość (qty) wynosi zero. Zwiększ ryzyko lub wybierz inny setup.")
            return None, None

        # === KROK 4: DYNAMICZNE OBLICZANIE POZIOMU TAKE PROFIT ===

        estimated_fees_usdt = notional_value * TAKER_FEE_RATE * 2
        target_gross_profit_usdt = TARGET_REWARD_USDT + estimated_fees_usdt
        price_change_for_tp = target_gross_profit_usdt / formatted_qty
        
        if alert_data.direction == "LONG":
            take_profit_price = entry_price + price_change_for_tp
        else:
            take_profit_price = entry_price - price_change_for_tp

        # === KROK 5: FINALIZACJA I ZŁOŻENIE ZLECENIA ===

        expected_price_loss = notional_value * sl_distance_percentage
        total_expected_loss = expected_price_loss + estimated_fees_usdt
        logger.info(f"[{symbol}] [Weryfikacja Ryzyka] Oczekiwana strata na cenie: ~{expected_price_loss:.4f} USDT. Szacowane opłaty: ~{estimated_fees_usdt:.4f} USDT.")
        logger.info(f"[{symbol}] [SUMA] Całkowita oczekiwana strata: ~{total_expected_loss:.4f} USDT (Cel: {TARGET_RISK_USDT} USDT).")
        
        expected_price_gain = notional_value * (abs(take_profit_price - entry_price) / entry_price)
        total_expected_profit = expected_price_gain - estimated_fees_usdt
        logger.info(f"[{symbol}] [Weryfikacja Zysku] Oczekiwany zysk na cenie: ~{expected_price_gain:.4f} USDT. Szacowane opłaty: ~{estimated_fees_usdt:.4f} USDT.")
        logger.info(f"[{symbol}] [SUMA] Całkowity oczekiwany zysk: ~{total_expected_profit:.4f} USDT (Cel: {TARGET_REWARD_USDT} USDT).")

        order_params = {
            "symbol": symbol,
            "side": alert_data.direction,
            "price": format_price(float(entry_price), str(tick_size)),
            "qty": str(formatted_qty),
            "leverage": str(int(instrument_info.get('max_leverage', 1.0))),
            "takeProfit": format_price(float(take_profit_price), str(tick_size)),
            "stopLoss": format_price(float(sl_price), str(tick_size))
        }
        
        logger.info(f"[{symbol}] [Finalne Zlecenie] Wartość Nominalna: ~{notional_value:.2f} USDT, Ilość (Qty): {formatted_qty}.")
        
        order_id = bybit_executor.place_limit_order(order_params)
        
        if order_id:
            return order_id, take_profit_price
        else:
            return None, None

    except Exception as e:
        logger.critical(f"[{symbol}] Nieoczekiwany, krytyczny błąd w logice przygotowywania zlecenia dla alertu {alert_id}: {e}", exc_info=True)
        return None, None

def process_new_alerts(newly_fetched_alerts: List[Dict[str, Any]], bybit_executor: BybitExecutor):
    if not newly_fetched_alerts:
        return
    logger.info(f"Rozpoczynam przetwarzanie {len(newly_fetched_alerts)} nowych alertów.")
    
    db = get_db() # Pobieramy instancję bazy danych na początku

    for alert_dict in newly_fetched_alerts:
        alert_id = alert_dict.get('id', 'N/A')
        symbol = "N/A"
        try:
            alert_data = AlertData.model_validate(alert_dict)
            symbol = alert_data.symbol

            # === ATOMOWA OPERACJA SPRAWDZENIA I PRZETWORZENIA ALERTU ===
            transaction = db.transaction()
            setup_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)

            @firestore.transactional
            def _process_alert_transaction(transaction, alert_data):
                # KROK 1: Sprawdzamy wewnątrz transakcji, czy na giełdzie jest już AKTYWNA POZYCJA
                if bybit_executor.has_open_position(alert_data.symbol):
                    logger.warning(f"[{alert_data.symbol}] ZABEZPIECZENIE GIEŁDOWE: Wykryto aktywną pozycję. Ignoruję alert {alert_id}.")
                    return "SKIP_HAS_POSITION"

                # KROK 2: Sprawdzamy, czy alert nie jest duplikatem tego, co już mamy w bazie
                setup_snapshot = setup_ref.get(transaction=transaction)
                if setup_snapshot.exists:
                    existing_setup = setup_snapshot.to_dict()
                    # Porównujemy timestamp, bo jest unikalny dla każdego sygnału z TradingView
                    if existing_setup.get('alert_data', {}).get('timestamp') == alert_data.timestamp:
                        logger.info(f"[{alert_data.symbol}] Alert {alert_id} jest już przetwarzany lub został przetworzony. Ignoruję.")
                        return "SKIP_DUPLICATE"
                
                # KROK 3: Jeśli to nowy, unikalny setup, czyścimy pole
                logger.info(f"--- [{alert_data.symbol}][Alert: {alert_id}] Wykryto nowy, unikalny setup. Rozpoczynam cykl wejścia. ---")
                
                # Anulujemy stare zlecenia OCZEKUJĄCE
                if not bybit_executor.cancel_all_open_orders_for_symbol(alert_data.symbol):
                    raise RuntimeError("Nie udało się wyczyścić zleceń oczekujących na giełdzie.")

                # Usuwamy stary wpis z 'open_trades' (jeśli istniał dla zlecenia oczekującego)
                open_trade_for_symbol = state_manager.get_open_trade_by_symbol(alert_data.symbol)
                if open_trade_for_symbol:
                    trade_id_to_delete = open_trade_for_symbol.get('trade_id')
                    logger.info(f"[{alert_data.symbol}] Usuwam stary wpis zlecenia oczekującego {trade_id_to_delete} z bazy.")
                    db.collection(constants.TRADE_COLLECTION).document(trade_id_to_delete).delete()

                # KROK 4: Tworzymy/nadpisujemy setup nowymi danymi w ramach tej samej transakcji
                new_setup_data = {
                    "alert_data": alert_data.model_dump(by_alias=True),
                    "is_position_open_on_this_setup": False,
                    "is_reset_needed_after_loss": False,
                    "entry_attempts": 0,
                    "updated_at": firestore.SERVER_TIMESTAMP
                }
                transaction.set(setup_ref, new_setup_data)
                return "PROCEED"

            # Uruchamiamy transakcję
            result = _process_alert_transaction(transaction, alert_data)

            # Jeśli transakcja zwróciła sygnał do pominięcia, przechodzimy do następnego alertu
            if result != "PROCEED":
                continue

            # === Złóż nowe zlecenie (tylko jeśli transakcja dała zielone światło) ===
            logger.info(f"[{symbol}] Giełda i baza danych są czyste. Składam nowe zlecenie.")
            order_id, new_tp_price = _prepare_and_place_order(alert_data, bybit_executor, alert_id)

            if order_id and new_tp_price:
                trade_id = str(uuid.uuid4())
                state_manager.create_open_trade(
                    trade_id=trade_id, symbol=symbol, direction=alert_data.direction,
                    ob_type="New Alert", entry_price=alert_data.entry, sl_price=alert_data.sl,
                    tp_price=float(new_tp_price), 
                    alert_data=alert_data, bybit_order_id=order_id
                )
                logger.info(f"[{symbol}][Alert: {alert_id}] SUKCES. Zlecenie {order_id} złożone i zapisane z TP={new_tp_price:.4f}.")
            else:
                logger.error(f"[{symbol}][Alert: {alert_id}] PORAŻKA. Nie udało się złożyć nowego zlecenia.")

        except Exception as e:
            logger.critical(f"[{symbol}] Nieoczekiwany błąd w głównej pętli alertu {alert_id}: {e}", exc_info=True)

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
                    order_id, new_tp_price = _prepare_and_place_order(setup.alert_data, bybit_executor)
                    
                    if order_id and new_tp_price:
                        trade_id = str(uuid.uuid4())
                        logger.info(f"[{symbol}] Zlecenie pomyślnie wysłane. Tworzę dokument w open_trades z trade_id: {trade_id} i bybit_order_id: {order_id}")
                        state_manager.create_open_trade(
                            trade_id=trade_id, symbol=symbol, direction=direction, ob_type=ob_type,
                            entry_price=entry_level, sl_price=sl_price, 
                            tp_price=float(new_tp_price),
                            alert_data=setup.alert_data, bybit_order_id=order_id
                        )
                    else:
                        logger.error(f"[{symbol}] Nie udało się uzyskać ID zlecenia od Bybit.")
        except ValidationError as e:
            logger.error(f"Błąd walidacji danych setupu dla {symbol}: {e}")
        except Exception as e:
            logger.error(f"Błąd podczas sprawdzania wejścia dla {symbol}: {e}", exc_info=True)

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