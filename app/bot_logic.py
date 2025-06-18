# TRADING_BOT/app/bot_logic.py (FINALNA WERSJA)

import logging
import requests
from typing import Dict
from . import state_manager
from .positions_logger import log_position_event
from .constants import BYBIT_API_URL_V5_TICKERS, BYBIT_DEFAULT_CATEGORY

logger = logging.getLogger(__name__)

# Ta funkcja pozostaje bez zmian, ale usuwamy z niej odwołania do kluczy API,
# ponieważ na razie nie rozwiązaliśmy problemu 403.
def get_all_prices_for_category(category: str = BYBIT_DEFAULT_CATEGORY) -> Dict[str, float]:
    """Pobiera ceny dla wszystkich symboli."""
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
                symbol = ticker.get("symbol")
                price_str = ticker.get("lastPrice")
                if symbol and price_str:
                    try:
                        all_prices[symbol] = float(price_str)
                    except ValueError:
                        pass
            logger.info(f"[GET_PRICES] Pomyślnie pobrano ceny dla {len(all_prices)} symboli.")
        else:
            logger.error(f"[GET_PRICES] Błąd API Bybit: {data.get('retMsg')}")
    except Exception as e:
        logger.error(f"[GET_PRICES] Nieoczekiwany błąd: {e}", exc_info=True)
    return all_prices


def run_trading_logic(all_prices: Dict[str, float]):
    """Główna pętla logiki, która monitoruje aktywne setupy."""
    active_symbols = state_manager.get_all_active_symbols()
    if not active_symbols:
        logger.info("Brak aktywnych setupów do monitorowania.")
        return

    logger.info(f"Monitoruję aktywne setupy dla symboli: {active_symbols}")

    for symbol in active_symbols:
        setup = state_manager.get_active_setup(symbol)
        if not setup:
            continue

        current_price = all_prices.get(symbol.replace(".P", ""))
        if current_price is None:
            logger.warning(f"[{symbol}] Brak aktualnej ceny. Pomijam cykl dla tego symbolu.")
            continue
        
        ob_data = setup['ob_data']
        is_new_ob = setup['is_new']
        is_position_open = setup['is_position_open']
        last_price = setup.get('last_known_price')
        
        try:
            direction = ob_data['direction'].lower()
            entry = float(ob_data['entry'])
            sl = float(ob_data['sl'])
            tp = float(ob_data['tp'])
        except (KeyError, ValueError) as e:
            logger.error(f"[{symbol}] Błąd danych w aktywnym setupie: {e}. Setup: {setup}")
            continue

        try:
            if is_position_open:
                # --- LOGIKA ZARZĄDZANIA OTWARTĄ POZYCJĄ ---
                ob_type = "New OB" if is_new_ob else "Old OB"
                closed_result = None
                if direction == 'long' and current_price >= tp: closed_result = "WIN"
                elif direction == 'long' and current_price <= sl: closed_result = "LOSE"
                elif direction == 'short' and current_price <= tp: closed_result = "WIN"
                elif direction == 'short' and current_price >= sl: closed_result = "LOSE"
                
                if closed_result:
                    logger.info(f"--- [ZAMKNIĘCIE] --- [{symbol}] | {ob_type} | Wynik: {closed_result} | Cena: {current_price}")
                    log_position_event(symbol, direction, "closed", ob_type, current_price, result=closed_result)
                    state_manager.set_position_status(symbol, is_open=False)
                    if is_new_ob:
                        state_manager.mark_setup_as_old(symbol)
            else:
                # --- LOGIKA WEJŚCIA W POZYCJĘ ---
                should_open = False
                if direction == 'long':
                    if (last_price is not None and last_price > entry and current_price <= entry) or (last_price is None and current_price <= entry):
                        should_open = True
                elif direction == 'short':
                    if (last_price is not None and last_price < entry and current_price >= entry) or (last_price is None and current_price >= entry):
                        should_open = True
                    
                if should_open:
                    ob_type = "New OB" if is_new_ob else "Old OB"
                    logger.info(f"--- [WEJŚCIE] --- [{symbol}] | {ob_type} | Cena: {current_price}")
                    log_position_event(symbol, direction, "opened", ob_type, current_price)
                    state_manager.set_position_status(symbol, is_open=True)

        finally:
            # Zawsze aktualizuj ostatnią znaną cenę na koniec cyklu dla tego symbolu
            state_manager.update_last_known_price(symbol, current_price)