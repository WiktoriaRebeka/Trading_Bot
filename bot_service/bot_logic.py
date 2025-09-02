import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import firestore
from pydantic import ValidationError

from shared_lib import constants
from shared_lib.firebase_client import get_db, get_symbols_to_watch_from_config
from shared_lib.leverage_calculator import format_price
from shared_lib.models import (
    AlertData,
    Kline,
    OpenTradeData,
    SetupData,
)

from bot_service import state_manager
from bot_service.pnl_logger import log_realized_trade
from bot_service.bybit_executor import BybitExecutor

logger = logging.getLogger(__name__)

def _prepare_and_place_order(alert_data: AlertData, bybit_executor: BybitExecutor, alert_id: str = 'N/A') -> Tuple[Optional[str], Optional[Decimal]]:
    """
    Przygotowuje i składa zlecenie, implementując precyzyjną strategię zarządzania ryzykiem.
    """
    symbol = alert_data.symbol
    logger.info(f"[{symbol}] --- Rozpoczynam kalkulację ryzyka dla alertu {alert_id} ---")
    try:
        instrument_info = bybit_executor.get_instrument_info(symbol)
        if not instrument_info:
            logger.error(f"[{symbol}] Nie udało się pobrać informacji o instrumencie. Przerywam.")
            return None, None
        tick_size = Decimal(instrument_info.get('tick_size'))
        qty_step = Decimal(instrument_info.get('qty_step'))
        min_order_qty = Decimal(instrument_info.get('min_order_qty'))
        entry_price = Decimal(str(alert_data.entry))
        sl_price = Decimal(str(alert_data.sl))
        TARGET_RISK_USDT = Decimal("2.50")
        TARGET_REWARD_USDT = Decimal("5.00")
        TAKER_FEE_RATE = Decimal("0.00055")
        MIN_SL_DISTANCE_PERCENT = Decimal("0.0005")
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
        notional_value = TARGET_RISK_USDT / total_cost_percentage
        target_qty = notional_value / entry_price
        if target_qty < min_order_qty:
            logger.warning(f"[{symbol}] Zlecenie odrzucone. Obliczona ilość ({target_qty}) < minimum giełdowe ({min_order_qty}).")
            return None, None
        formatted_qty = target_qty.quantize(qty_step, rounding=ROUND_DOWN)
        if formatted_qty <= 0:
            logger.error(f"[{symbol}] Po zaokrągleniu ilość (qty) wynosi zero. Zwiększ ryzyko lub wybierz inny setup.")
            return None, None
        estimated_fees_usdt = notional_value * TAKER_FEE_RATE * 2
        target_gross_profit_usdt = TARGET_REWARD_USDT + estimated_fees_usdt
        price_change_for_tp = target_gross_profit_usdt / formatted_qty
        if alert_data.direction == "LONG":
            take_profit_price = entry_price + price_change_for_tp
        else:
            take_profit_price = entry_price - price_change_for_tp
        order_params = {
            "symbol": symbol, "side": alert_data.direction, "price": format_price(float(entry_price), str(tick_size)),
            "qty": str(formatted_qty), "leverage": str(int(instrument_info.get('max_leverage', 1.0))),
            "takeProfit": format_price(float(take_profit_price), str(tick_size)), "stopLoss": format_price(float(sl_price), str(tick_size))
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
    if not newly_fetched_alerts: return
    logger.info(f"Rozpoczynam przetwarzanie {len(newly_fetched_alerts)} nowych alertów.")
    db = get_db()
    for alert_dict in newly_fetched_alerts:
        alert_id = alert_dict.get('id', 'N/A')
        symbol = "N/A"
        try:
            alert_data = AlertData.model_validate(alert_dict)
            symbol = alert_data.symbol
            transaction = db.transaction()
            setup_ref = db.collection(constants.SETUP_COLLECTION).document(symbol)
            @firestore.transactional
            def _process_alert_transaction(transaction, alert_data):
                if bybit_executor.has_open_position(alert_data.symbol):
                    logger.warning(f"[{alert_data.symbol}] ZABEZPIECZENIE GIEŁDOWE: Wykryto aktywną pozycję. Ignoruję alert {alert_id}.")
                    return "SKIP_HAS_POSITION"
                setup_snapshot = setup_ref.get(transaction=transaction)
                if setup_snapshot.exists:
                    existing_setup = setup_snapshot.to_dict()
                    if existing_setup.get('alert_data', {}).get('timestamp') == alert_data.timestamp:
                        logger.info(f"[{alert_data.symbol}] Alert {alert_id} jest już przetwarzany lub został przetworzony. Ignoruję.")
                        return "SKIP_DUPLICATE"
                logger.info(f"--- [{alert_data.symbol}][Alert: {alert_id}] Wykryto nowy, unikalny setup. Rozpoczynam cykl wejścia. ---")
                if not bybit_executor.cancel_all_open_orders_for_symbol(alert_data.symbol):
                    raise RuntimeError("Nie udało się wyczyścić zleceń oczekujących na giełdzie.")
                open_trade_for_symbol = state_manager.get_open_trade_by_symbol(alert_data.symbol)
                if open_trade_for_symbol:
                    trade_id_to_delete = open_trade_for_symbol.get('trade_id')
                    logger.info(f"[{alert_data.symbol}] Usuwam stary wpis zlecenia oczekującego {trade_id_to_delete} z bazy.")
                    db.collection(constants.TRADE_COLLECTION).document(trade_id_to_delete).delete()
                new_setup_data = {
                    "alert_data": alert_data.model_dump(by_alias=True), "is_position_open_on_this_setup": False,
                    "is_reset_needed_after_loss": False, "entry_attempts": 0, "updated_at": firestore.SERVER_TIMESTAMP
                }
                transaction.set(setup_ref, new_setup_data)
                return "PROCEED"
            result = _process_alert_transaction(transaction, alert_data)
            if result != "PROCEED": continue
            logger.info(f"[{symbol}] Giełda i baza danych są czyste. Składam nowe zlecenie.")
            order_id, new_tp_price = _prepare_and_place_order(alert_data, bybit_executor, alert_id)
            if order_id and new_tp_price:
                trade_id = str(uuid.uuid4())
                state_manager.create_open_trade(
                    trade_id=trade_id, symbol=symbol, direction=alert_data.direction, ob_type="New Alert",
                    entry_price=alert_data.entry, sl_price=alert_data.sl, tp_price=float(new_tp_price), 
                    alert_data=alert_data, bybit_order_id=order_id
                )
                logger.info(f"[{symbol}][Alert: {alert_id}] SUKCES. Zlecenie {order_id} złożone i zapisane z TP={new_tp_price:.4f}.")
            else:
                logger.error(f"[{symbol}][Alert: {alert_id}] PORAŻKA. Nie udało się złożyć nowego zlecenia.")
        except Exception as e:
            logger.critical(f"[{symbol}] Nieoczekiwany błąd w głównej pętli alertu {alert_id}: {e}", exc_info=True)

def _handle_setups(klines_data: Dict[str, Kline], active_setups_docs: List[Dict[str, Any]], bybit_executor: BybitExecutor):
    if not active_setups_docs: return
    logger.info(f"Sprawdzam {len(active_setups_docs)} aktywnych setupów (z głównej pętli logiki).")
    for setup_doc in active_setups_docs:
        symbol = setup_doc.get('alert_data', {}).get('symbol')
        if not symbol: continue
        try:
            setup = SetupData.model_validate(setup_doc)
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
                    finalize_trade(None, closed_result, close_price)
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

def finalize_trade(trade: Optional[OpenTradeData], closed_result: str, close_price: float):
    """
    Funkcja-zaślepka. Wcześniej tworzyła 'ducha'. Teraz nie robi nic.
    Logika P&L została przeniesiona do _handle_manage_open_trades.
    """
    symbol = trade.symbol if trade else "N/A"
    trade_id = trade.trade_id if trade else "N/A"
    logger.info(f"--- [FINALIZACJA] --- [{symbol}] | ID: {trade_id} | Wynik: {closed_result}. Logika 'ducha' jest wyłączona.")
    pass
           
def _handle_manage_open_trades(klines_data: Dict[str, Kline], open_trades_docs: List[Dict[str, Any]], bybit_executor: BybitExecutor):
    if not open_trades_docs: return
    logger.info(f"Zarządzam {len(open_trades_docs)} otwartymi pozycjami (z głównej pętli logiki).")
    for trade_doc in open_trades_docs:
        trade_id = trade_doc.get('trade_id')
        if not trade_id: continue
        try:
            trade = OpenTradeData.model_validate(trade_doc)
            latest_kline = klines_data.get(trade.symbol)
            if not latest_kline:
                logger.warning(f"[{trade_id}] Brak danych kline dla {trade.symbol}. Pomijam.")
                continue
            closed_result, close_price, close_reason = None, None, None
            if trade.direction == 'LONG':
                if latest_kline.low <= trade.sl_price: 
                    closed_result, close_price, close_reason = "LOSE", trade.sl_price, "STOP_LOSS"
                elif latest_kline.high >= trade.tp_price: 
                    closed_result, close_price, close_reason = "WIN", trade.tp_price, "TAKE_PROFIT"
            elif trade.direction == 'SHORT':
                if latest_kline.high >= trade.sl_price: 
                    closed_result, close_price, close_reason = "LOSE", trade.sl_price, "STOP_LOSS"
                elif latest_kline.low <= trade.tp_price: 
                    closed_result, close_price, close_reason = "WIN", trade.tp_price, "TAKE_PROFIT"
            if closed_result:
                logger.info(f"--- [DECYZJA: ZAMKNIĘCIE] --- [{trade.symbol}] | ID: {trade_id} | Wynik: {closed_result}")
                logger.info(f"[{trade_id}] Pozycja oznaczona jako zamknięta. Próbuję pobrać dane P&L z Bybit...")
                pnl_data_from_bybit = bybit_executor.get_last_closed_pnl(trade.symbol)
                if pnl_data_from_bybit:
                    logger.info(f"[{trade_id}] Pomyślnie pobrano dane P&L z Bybit: {pnl_data_from_bybit}")
                    real_pnl = pnl_data_from_bybit['closed_pnl']
                    planned_risk_usdt = 2.50
                    realized_rr = 0.0
                    if closed_result == "WIN" and planned_risk_usdt > 0:
                        realized_rr = real_pnl / planned_risk_usdt
                    bq_pnl_data = {
                        "trade_id": trade.trade_id, "bybit_order_id": trade.bybit_order_id, "symbol": trade.symbol,
                        "direction": trade.direction, "entry_price_planned": trade.entry_price, "stop_loss_price": trade.sl_price,
                        "realized_pnl_usdt": real_pnl, "commission_usdt": None, "final_result": closed_result,
                        "realized_rr": round(realized_rr, 4), "timestamp_entry": datetime.fromtimestamp(trade.opened_at_ms / 1000, tz=timezone.utc),
                        "timestamp_close": datetime.fromtimestamp(pnl_data_from_bybit['updated_time'] / 1000, tz=timezone.utc),
                        "close_reason": close_reason,
                    }
                    logger.info(f"[{trade_id}] Przygotowano dane do zapisu w BigQuery. Wywołuję pnl_logger...")
                    log_realized_trade(bq_pnl_data)
                else:
                    logger.error(f"[{trade_id}] KRYTYCZNY BŁĄD: Nie udało się pobrać danych P&L z Bybit! Zapis do tabeli 'realized_trades_pnl' nie zostanie wykonany.")
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

def run_trading_logic(bybit_executor: BybitExecutor):
    logger.info("--- ROZPOCZYNAM GŁÓWNĄ PĘTLĘ LOGIKI ---")
    
    symbols_to_watch = set(get_symbols_to_watch_from_config())
    active_setups = {doc.id: doc.to_dict() for doc in state_manager.get_all_active_setups()}
    open_trades = {doc.to_dict()['symbol']: doc.to_dict() for doc in list(state_manager.get_all_open_trades())}
    
    symbols_to_watch.update(active_setups.keys(), open_trades.keys())
    valid_symbols = {s for s in symbols_to_watch if isinstance(s, str) and s}

    if not valid_symbols:
        logger.info("Brak symboli do monitorowania. Kończę cykl.")
        return

    klines_data = {
        symbol: Kline.model_validate(data) 
        for symbol, data in state_manager.get_latest_klines_from_cache(list(valid_symbols)).items()
    }

    for symbol in valid_symbols:
        try:
            latest_kline = klines_data.get(symbol)
            if not latest_kline:
                continue

            if bybit_executor.has_open_position(symbol):
                logger.info(f"[{symbol}] Wykryto aktywną pozycję na giełdzie.")
                trade_data = open_trades.get(symbol)
                if trade_data:
                    _handle_manage_open_trades(klines_data, [trade_data], bybit_executor)
                else:
                    logger.error(f"[{symbol}] KRYTYCZNY BŁĄD: Wykryto pozycję na giełdzie, ale brak jej w bazie 'open_trades'!")
                continue

            setup_data = active_setups.get(symbol)
            if setup_data:
                _handle_setups(klines_data, [setup_data], bybit_executor)

        except Exception as e:
            logger.error(f"[{symbol}] Nieoczekiwany błąd podczas przetwarzania symbolu: {e}", exc_info=True)

    logger.info("--- ZAKOŃCZONO GŁÓWNĄ PĘTLĘ LOGIKI ---")