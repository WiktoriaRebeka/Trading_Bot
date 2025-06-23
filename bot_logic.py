# /trading_bot/bot_logic.py

import logging
import requests
import uuid
from typing import Dict, Optional, Any
from datetime import datetime, timezone

# Importy modułów aplikacji
import state_manager
from bigquery_logger import log_trade_to_bigquery
from constants import BYBIT_API_URL_V5_TICKERS, BYBIT_DEFAULT_CATEGORY

logger = logging.getLogger(__name__)


def get_all_prices_for_category(category: str = BYBIT_DEFAULT_CATEGORY) -> Dict[str, float]:
    """Pobiera wszystkie ceny tickerów dla danej kategorii z API Bybit."""
    logger.info(f"[GET_PRICES] Rozpoczynam pobieranie cen dla kategorii: {category}")
    params = {"category": category}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    all_prices = {}
    try:
        response = requests.get(BYBIT_API_URL_V5_TICKERS, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") == 0 and data.get("result") and data["result"].get("list"):
            for ticker in data["result"]["list"]:
                symbol, price_str = ticker.get("symbol"), ticker.get("lastPrice")
                if symbol and price_str:
                    try: all_prices[symbol] = float(price_str)
                    except ValueError: pass
            logger.info(f"[GET_PRICES] Pomyślnie pobrano ceny dla {len(all_prices)} symboli.")
        else:
            logger.error(f"[GET_PRICES] Błąd API Bybit: {data.get('retMsg')}")
    except Exception as e:
        logger.error(f"[GET_PRICES] Nieoczekiwany błąd: {e}", exc_info=True)
    return all_prices


def run_trading_logic(all_prices: Dict[str, float]):
    """Główna pętla logiki, która monitoruje aktywne setupy."""
    # Kopiujemy listę symboli, aby uniknąć problemów z modyfikacją słownika w trakcie iteracji
    active_symbols = state_manager.get_all_active_symbols()
    if not active_symbols:
        logger.info("Brak aktywnych setupów do monitorowania.")
        return

    logger.info(f"Monitoruję aktywne setupy dla symboli: {active_symbols}")

    for symbol in list(active_symbols): # Iterujemy po kopii kluczy
        try:
            setup = state_manager.get_active_setup(symbol)
            if not setup: continue

            api_symbol = symbol.replace('.P', '')
            current_price = all_prices.get(api_symbol)

            if current_price is None:
                logger.warning(f"[{symbol}] Brak aktualnej ceny w danych z API. Pomijam.")
                continue

            is_position_open = setup.get('is_position_open', False)

            if is_position_open:
                # --- LOGIKA ZARZĄDZANIA OTWARTĄ POZYCJĄ ---
                position_data = setup.get('position_data')
                if not position_data:
                    logger.warning(f"[{symbol}] Pozycja oznaczona jako otwarta, ale brak 'position_data'. Pomijam.")
                    continue

                direction = position_data['direction']
                main_tp_price = position_data['main_tp_price']
                sl_price = position_data['sl_price']
                
                # --- Aktualizacja maksymalnego profitu i flag RR ---
                max_profit_price = position_data.get('max_profit_price', current_price)
                if direction == 'long': max_profit_price = max(max_profit_price, current_price)
                else: max_profit_price = min(max_profit_price, current_price)
                state_manager.update_max_profit_price(symbol, max_profit_price)

                rr_targets = position_data.get('rr_targets', {})
                rr_flags = position_data.get('rr_achieved_flags', {})
                for rr_key, tp_value in rr_targets.items():
                    if tp_value is not None and not rr_flags.get(rr_key):
                        if (direction == 'long' and current_price >= tp_value) or \
                           (direction == 'short' and current_price <= tp_value):
                            logger.info(f"[{symbol}] OSIĄGNIĘTO CEL RR: {rr_key} przy cenie {current_price}")
                            rr_flags[rr_key] = True
                state_manager.update_rr_flags(symbol, rr_flags)

                # --- Sprawdzenie warunków zamknięcia transakcji ---
                closed_result = None
                if (direction == 'long' and current_price >= main_tp_price): closed_result = "WIN"
                elif (direction == 'long' and current_price <= sl_price): closed_result = "LOSE"
                elif (direction == 'short' and current_price <= main_tp_price): closed_result = "WIN"
                elif (direction == 'short' and current_price >= sl_price): closed_result = "LOSE"
                
                if closed_result:
                    logger.info(f"--- [ZAMKNIĘCIE: {closed_result}] --- [{symbol}] | Cena: {current_price}")
                    
                    # --- LOGOWANIE DO BIGQUERY ---
                    entry_price = position_data['entry_price']
                    risk_amount_usd = abs(entry_price - sl_price)
                    max_profit_achieved_usd = abs(max_profit_price - entry_price)
                    rr_achieved = (max_profit_achieved_usd / risk_amount_usd) if risk_amount_usd > 0 else 0.0

                    trade_data = {
                        "trade_id": position_data['trade_id'],
                        "timestamp_entry": position_data['timestamp_entry'],
                        "timestamp_close": datetime.now(timezone.utc).isoformat(),
                        "symbol": symbol,
                        "direction": direction.upper(),
                        "main_result": closed_result,
                        "ob_type": position_data['ob_type'],
                        "risk_amount_usd": risk_amount_usd,
                        "max_profit_achieved_usd": max_profit_achieved_usd,
                        "rr_achieved": rr_achieved,
                        "rr_1_0_win": rr_flags.get("tp_1_0", False),
                        "rr_1_5_win": rr_flags.get("tp_1_5", False),
                        "rr_2_0_win": rr_flags.get("tp_2_0", False),
                        "rr_3_0_win": rr_flags.get("tp_3_0", False),
                        "rr_4_0_win": rr_flags.get("tp_4_0", False),
                        "rr_5_0_win": rr_flags.get("tp_5_0", False)
                    }
                    log_trade_to_bigquery(trade_data)
                    
                    # === KLUCZOWA POPRAWKA LOGIKI ===
                    # Całkowicie usuwamy setup ze stanu, aby zakończyć jego cykl życia.
                    state_manager.remove_setup(symbol)
                    # ==============================
            
            else:
                # --- LOGIKA WEJŚCIA W POZYCJĘ ---
                ob_data = setup.get('ob_data')
                if not ob_data: continue
                
                last_price = setup.get('last_known_price')

                try:
                    direction = ob_data['direction'].lower()
                    entry = float(ob_data['entry'])
                    sl = float(ob_data['sl'])
                    tp = float(ob_data['tp'])
                except (KeyError, ValueError, TypeError) as e:
                    logger.error(f"[{symbol}] Błąd pól w 'ob_data' (np. brak klucza lub zła wartość): {e}. Pomijam setup. Dane: {ob_data}")
                    # Po błędzie danych usuwamy wadliwy setup, aby nie próbować go w kółko
                    state_manager.remove_setup(symbol)
                    continue

                should_open = False
                if direction == 'long' and (last_price is not None and last_price > entry and current_price <= entry): should_open = True
                elif direction == 'short' and (last_price is not None and last_price < entry and current_price >= entry): should_open = True
                
                if should_open:
                    ob_type = "New OB" if setup['is_new'] else "Old OB"
                    trade_id = str(uuid.uuid4())
                    logger.info(f"--- [DECYZJA: WEJŚCIE] --- [{symbol}] | {ob_type} | Cena: {current_price} | ID: {trade_id}")
                    
                    trade_details = {
                        "trade_id": trade_id,
                        "timestamp_entry": datetime.now(timezone.utc).isoformat(),
                        "direction": direction,
                        "ob_type": ob_type,
                        "entry_price": current_price,
                        "sl_price": sl,
                        "main_tp_price": tp,
                        "max_profit_price": current_price,
                        "rr_targets": {
                            "tp_1_0": ob_data.get("tp_1_0"), "tp_1_5": ob_data.get("tp_1_5"),
                            "tp_2_0": ob_data.get("tp_2_0"), "tp_3_0": ob_data.get("tp_3_0"),
                            "tp_4_0": ob_data.get("tp_4_0"), "tp_5_0": ob_data.get("tp_5_0")
                        },
                        "rr_achieved_flags": {
                            "tp_1_0": False, "tp_1_5": False, "tp_2_0": False,
                            "tp_3_0": False, "tp_4_0": False, "tp_5_0": False
                        }
                    }
                    state_manager.set_position_status(symbol, is_open=True, trade_details=trade_details)
                    
                    # Oznaczamy setup jako "stary" zaraz po wejściu w pozycję
                    state_manager.mark_setup_as_old(symbol)
            
            state_manager.update_last_known_price(symbol, current_price)

        except Exception as e:
            logger.error(f"KRYTYCZNY BŁĄD podczas przetwarzania symbolu [{symbol}]. Pomijam. Błąd: {e}", exc_info=True)
            # W przypadku nieoczekiwanego błędu, usuwamy setup, aby uniknąć pętli błędów
            state_manager.remove_setup(symbol)
            continue