import logging
import requests
from typing import Dict
from . import state_manager
from .positions_logger import log_position_event
from .constants import BYBIT_API_URL_V5_TICKERS, BYBIT_DEFAULT_CATEGORY

logger = logging.getLogger(__name__)

# Funkcja get_all_prices_for_category pozostaje bez zmian...
def get_all_prices_for_category(category: str = BYBIT_DEFAULT_CATEGORY) -> Dict[str, float]:
    # ... (cała funkcja bez zmian) ...
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
        
        # --- ZMIANA 1: Poprawione pobieranie nazwy symbolu dla API ---
        # Usuwamy ".P", ale też sprawdzamy, czy symbol już go nie ma.
        api_symbol = symbol.replace('.P', '')
        current_price = all_prices.get(api_symbol)

        # --- DODANE LOGOWANIE: Sprawdzamy, czy mamy cenę ---
        logger.info(f"[{symbol}] Sprawdzam. Symbol dla API: '{api_symbol}'. Znaleziona cena: {current_price}")

        if current_price is None:
            logger.warning(f"[{symbol}] Brak aktualnej ceny w danych z API. Pomijam cykl dla tego symbolu.")
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
                # --- LOGIKA ZARZĄDZANIA OTWARTĄ POZYCJĄ (bez zmian) ---
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
                # --- LOGIKA WEJŚCIA W POZYCJĘ (z nowym, szczegółowym logowaniem) ---
                should_open = False
                logger.info(f"[{symbol}] Analiza wejścia. Pozycja nie jest otwarta. Kierunek: {direction.upper()}. Cena wejścia (entry): {entry}. Aktualna cena: {current_price}. Ostatnia znana cena: {last_price}")

                if direction == 'long':
                    # Warunek 1: Cena przeszła z góry na dół przez poziom wejścia
                    condition1_met = (last_price is not None and last_price > entry and current_price <= entry)
                    # Warunek 2: Pierwsze sprawdzenie (brak ostatniej ceny) i cena jest już poniżej wejścia
                    condition2_met = (last_price is None and current_price <= entry)
                    logger.info(f"[{symbol}] [LONG] Sprawdzanie warunków: Przekroczenie z góry ({condition1_met}), Pierwsze sprawdzenie ({condition2_met})")
                    if condition1_met or condition2_met:
                        should_open = True
                
                elif direction == 'short':
                    # Warunek 1: Cena przeszła z dołu na górę przez poziom wejścia
                    condition1_met = (last_price is not None and last_price < entry and current_price >= entry)
                    # Warunek 2: Pierwsze sprawdzenie (brak ostatniej ceny) i cena jest już powyżej wejścia
                    condition2_met = (last_price is None and current_price >= entry)
                    logger.info(f"[{symbol}] [SHORT] Sprawdzanie warunków: Przekroczenie z dołu ({condition1_met}), Pierwsze sprawdzenie ({condition2_met})")
                    if condition1_met or condition2_met:
                        should_open = True
                    
                if should_open:
                    ob_type = "New OB" if is_new_ob else "Old OB"
                    logger.info(f"--- [DECYZJA: WEJŚCIE] --- [{symbol}] | {ob_type} | Cena: {current_price}")
                    log_position_event(symbol, direction, "opened", ob_type, current_price)
                    state_manager.set_position_status(symbol, is_open=True)
                else:
                    logger.info(f"[{symbol}] DECYZJA: Brak wejścia w tym cyklu.")

        finally:
            # Zawsze aktualizuj ostatnią znaną cenę na koniec cyklu dla tego symbolu
            state_manager.update_last_known_price(symbol, current_price)