# Lokalizacja: bot_service/bot_logic.py

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import firestore
from pydantic import ValidationError

from shared_lib import constants
from shared_lib.firebase_client import get_db
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

def _prepare_and_place_order(alert_data: AlertData, bybit_executor: BybitExecutor, alert_id: str = 'N/A') -> Tuple[Optional[str], Optional[Decimal], Optional[Decimal]]:
    symbol = alert_data.symbol
    logger.info(f"[{symbol}] --- ROZPOCZYNAM PRZYGOTOWANIE ZLECENIA (PROSTA LOGIKA) ---")
    try:
        # --- ETAP 1: POBIERANIE DANYCH I WALIDACJA ---
        instrument_info = bybit_executor.get_instrument_info(symbol)
        if not instrument_info:
            logger.error(f"[{symbol}] BŁĄD: Nie udało się pobrać informacji o instrumencie.")
            return None, None, None
            
        tick_size = Decimal(instrument_info.get('tick_size'))
        qty_step = Decimal(instrument_info.get('qty_step'))
        max_leverage = Decimal(instrument_info.get('max_leverage', '1'))
        
        entry_price = Decimal(str(alert_data.entry))
        sl_price = Decimal(str(alert_data.sl))
        take_profit_price = Decimal(str(alert_data.tp_2_0))
        
        logger.info(f"[{symbol}] Dane z alertu: Entry={entry_price}, SL={sl_price}, TP (z tp_2_0)={take_profit_price}")

        if (alert_data.direction == "LONG" and sl_price >= entry_price) or \
           (alert_data.direction == "SHORT" and sl_price <= entry_price):
            logger.error(f"[{symbol}] BŁĄD WALIDACJI: Nielogiczny poziom SL. Zlecenie odrzucone.")
            return None, None, None

        MIN_SL_DISTANCE_PERCENT = Decimal("0.002") # 0.2%
        if entry_price > 0:
            sl_distance_percentage = abs(entry_price - sl_price) / entry_price
            if sl_distance_percentage < MIN_SL_DISTANCE_PERCENT:
                logger.warning(
                    f"[{symbol}] Zlecenie odrzucone. Odległość SL ({sl_distance_percentage:.4%}) "
                    f"jest mniejsza niż wymagane minimum ({MIN_SL_DISTANCE_PERCENT:.4%})."
                )
                return None, None, None
        
        logger.info(f"[{symbol}] Walidacja odległości SL zakończona pomyślnie.")

        # --- ETAP 2: OBLICZANIE WIELKOŚCI POZYCJI (TWOJA METODA) ---
        MARGIN_PER_TRADE = Decimal("10.00")
        
        notional_value = MARGIN_PER_TRADE * max_leverage
        target_qty = notional_value / entry_price
        formatted_qty = target_qty.quantize(qty_step, rounding=ROUND_DOWN)

        if formatted_qty <= 0:
            logger.error(f"[{symbol}] BŁĄD: Obliczona ilość (qty) jest zerowa lub ujemna. Przerywam.")
            return None, None, None
        
        logger.info(f"[{symbol}] Obliczenia Qty: Kapitał={MARGIN_PER_TRADE} USDT * Lewar={max_leverage}x -> Wartość Nominalna={notional_value:.2f} USDT -> Ilość={formatted_qty}")

        # --- ETAP 3: WYSŁANIE ZLECENIA ---
        order_params = {
            "symbol": symbol, "side": alert_data.direction, "price": format_price(float(entry_price), str(tick_size)),
            "qty": str(formatted_qty), "leverage": str(int(max_leverage)),
            "takeProfit": format_price(float(take_profit_price), str(tick_size)), "stopLoss": format_price(float(sl_price), str(tick_size))
        }
        
        logger.info(f"[{symbol}] --- FINALNE PARAMETRY WYSYŁANE DO BYBIT ---")
        logger.info(f"[{symbol}] {order_params}")
        
        order_id = bybit_executor.place_limit_order(order_params)
        
        if order_id:
            return order_id, take_profit_price, sl_price
        else:
            return None, None, None
            
    except Exception as e:
        logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD w _prepare_and_place_order dla alertu {alert_id}: {e}", exc_info=True)
        return None, None, None

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
                    logger.warning(f"[{alert_data.symbol}] ZABEZPIECZENIE: Aktywna pozycja. Ignoruję alert {alert_id}.")
                    return "SKIP_HAS_POSITION"
                setup_snapshot = setup_ref.get(transaction=transaction)
                if setup_snapshot.exists:
                    existing_setup = setup_snapshot.to_dict()
                    if existing_setup.get('alert_data', {}).get('timestamp') == alert_data.timestamp:
                        logger.info(f"[{alert_data.symbol}] Alert {alert_id} jest duplikatem. Ignoruję.")
                        return "SKIP_DUPLICATE"
                logger.info(f"--- [{alert_data.symbol}][Alert: {alert_id}] Nowy, unikalny setup. Czyszczę pole. ---")
                if not bybit_executor.cancel_all_open_orders_for_symbol(alert_data.symbol):
                    raise RuntimeError("Nie udało się anulować starych zleceń.")
                open_trade_for_symbol = state_manager.get_open_trade_by_symbol(alert_data.symbol)
                if open_trade_for_symbol:
                    trade_id_to_delete = open_trade_for_symbol.get('trade_id')
                    logger.info(f"[{alert_data.symbol}] Usuwam stary wpis zlecenia {trade_id_to_delete}.")
                    db.collection(constants.TRADE_COLLECTION).document(trade_id_to_delete).delete()
                new_setup_data = {
                    "alert_data": alert_data.model_dump(by_alias=True), "is_position_open_on_this_setup": False,
                    "is_reset_needed_after_loss": False, "entry_attempts": 0, "updated_at": firestore.SERVER_TIMESTAMP
                }
                transaction.set(setup_ref, new_setup_data)
                return "PROCEED"
            result = _process_alert_transaction(transaction, alert_data)
            if result != "PROCEED": continue

            logger.info(f"[{symbol}] Składam nowe zlecenie.")
            
            order_id, new_tp_price, final_sl_price = _prepare_and_place_order(alert_data, bybit_executor, alert_id)
            
            if order_id and new_tp_price and final_sl_price:
                trade_id = str(uuid.uuid4())
                state_manager.create_open_trade(
                    trade_id=trade_id, symbol=symbol, direction=alert_data.direction, ob_type="New Alert",
                    entry_price=alert_data.entry, sl_price=float(final_sl_price), tp_price=float(new_tp_price), 
                    alert_data=alert_data, bybit_order_id=order_id
                )
                logger.info(f"[{symbol}][Alert: {alert_id}] SUKCES. Zlecenie {order_id} złożone i zapisane.")
            else:
                logger.error(f"[{symbol}][Alert: {alert_id}] PORAŻKA. Nie udało się złożyć nowego zlecenia.")
        except Exception as e:
            logger.critical(f"[{symbol}] Nieoczekiwany błąd w pętli alertu {alert_id}: {e}", exc_info=True)

def run_trading_logic(bybit_executor: BybitExecutor):
    # Ta funkcja jest celowo pusta. Cała logika wejścia jest teraz w `process_new_alerts`.
    # W przyszłości można tu dodać logikę zarządzania pozycją (np. Trailing Stop).
    logger.info("--- Pętla `run_trading_logic` jest obecnie wyłączona. ---")
    pass

def sync_pnl_history(bybit_executor: BybitExecutor):
    logger.info("--- ROZPOCZĘCIE CYKLU SYNCHRONIZACJI P&L ---")
    
    all_trades_in_db_docs = list(state_manager.get_all_open_trades())
    if not all_trades_in_db_docs:
        logger.info("Brak otwartych transakcji w bazie do synchronizacji P&L. Kończę synchronizację.")
        return

    all_trades_in_db = [doc.to_dict() for doc in all_trades_in_db_docs]
    symbols_to_check = {trade['symbol'] for trade in all_trades_in_db}
    
    last_sync_ts_ms = state_manager.load_last_pnl_sync_timestamp()
    newest_processed_ts = last_sync_ts_ms

    for symbol in symbols_to_check:
        closed_positions = bybit_executor.get_closed_pnl_since(symbol, start_time_ms=(last_sync_ts_ms - 1000))
        
        for position_data in closed_positions:
            try:
                closed_time_ms = int(position_data.get("updatedTime", 0))
                if closed_time_ms <= last_sync_ts_ms:
                    continue

                trade_in_db = next((t for t in all_trades_in_db if t.get('symbol') == symbol), None)
                
                if not trade_in_db:
                    logger.warning(f"[{symbol}] Znaleziono zamkniętą pozycję w Bybit, ale brak jej odpowiednika w 'open_trades'. Może to być transakcja ręczna. Pomijam.")
                    continue

                real_pnl = float(position_data.get("closedPnl", 0.0))
                final_result = "WIN" if real_pnl > 0 else "LOSE"
                is_loss = final_result == "LOSE"
                
                # Proste obliczenie R:R na podstawie stałego ryzyka
                planned_risk_usdt = 2.50
                realized_rr = 0.0
                if planned_risk_usdt > 0:
                    realized_rr = real_pnl / planned_risk_usdt

                bq_pnl_data = {
                    "trade_id": trade_in_db['trade_id'],
                    "bybit_order_id": trade_in_db['bybit_order_id'],
                    "symbol": symbol,
                    "direction": trade_in_db['direction'],
                    "entry_price_planned": trade_in_db['entry_price'],
                    "stop_loss_price": trade_in_db['sl_price'],
                    "realized_pnl_usdt": real_pnl,
                    "commission_usdt": None,
                    "final_result": final_result,
                    "realized_rr": round(realized_rr, 4),
                    "timestamp_entry": datetime.fromtimestamp(trade_in_db['opened_at_ms'] / 1000, tz=timezone.utc),
                    "timestamp_close": datetime.fromtimestamp(closed_time_ms / 1000, tz=timezone.utc),
                    "close_reason": "CLOSED_ON_BYBIT",
                }
                
                log_realized_trade(bq_pnl_data)
                state_manager.reset_setup_after_trade_close(symbol, is_loss=is_loss)
                state_manager.delete_open_trade(trade_in_db['trade_id'])
                
                if closed_time_ms > newest_processed_ts:
                    newest_processed_ts = closed_time_ms

            except Exception as e:
                logger.error(f"[{symbol}] Błąd podczas przetwarzania rekordu P&L: {e}", exc_info=True)

    if newest_processed_ts > last_sync_ts_ms:
        state_manager.save_last_pnl_sync_timestamp(newest_processed_ts)
        
    logger.info("--- ZAKOŃCZONO CYKL SYNCHRONIZACJI P&L ---")