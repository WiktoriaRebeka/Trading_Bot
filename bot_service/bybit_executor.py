# Lokalizacja: bot_service/bybit_executor.py

import logging
import time
import hmac
import hashlib
import json
from typing import Dict, Any, Optional
from urllib.parse import urlencode
from decimal import Decimal, ROUND_DOWN
import requests
from requests.exceptions import RequestException

from shared_lib import constants
from shared_lib.config import config

logger = logging.getLogger(__name__)

class BybitAPIError(Exception):
    def __init__(self, ret_code: int, ret_msg: str):
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        super().__init__(f"Bybit API Error: [Code: {ret_code}] {ret_msg}")

def format_quantity(quantity: float, qty_step: str) -> str:
    qty_decimal = Decimal(str(quantity))
    step_decimal = Decimal(qty_step)
    formatted_qty = qty_decimal.quantize(step_decimal, rounding=ROUND_DOWN)
    return str(formatted_qty)

class BybitExecutor:
    def __init__(self, api_key: str, api_secret: str):
        if not api_key or not api_secret:
            raise ValueError("Klucze API Bybit nie mogą być puste.")
        
        self.api_key: str = api_key
        self.api_secret: str = api_secret
        self.base_url: str = "https://api-demo.bybit.com"
        self.session = requests.Session()

    def _send_request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        timestamp = str(int(time.time() * 1000))
        recv_window = "10000"
        
        if not self.api_key or not self.api_secret:
            logger.critical("KRYTYCZNY BŁĄD: Próba wysłania żądania z pustymi kluczami API!")
            raise RuntimeError("Klucze API w instancji BybitExecutor są puste.")

        try:
            if method.upper() == 'GET':
                query_string = urlencode(params, doseq=True) if params else ""
                to_sign = timestamp + self.api_key + recv_window + query_string
                signature = hmac.new(bytes(self.api_secret, "utf-8"), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
                
                headers = {
                    'X-BAPI-API-KEY': self.api_key,
                    'X-BAPI-TIMESTAMP': timestamp,
                    'X-BAPI-SIGN': signature,
                    'X-BAPI-RECV-WINDOW': recv_window,
                    'Content-Type': 'application/json'
                }
                
                response = self.session.get(self.base_url + endpoint, headers=headers, params=params, timeout=15)

            else: # POST
                payload_string = json.dumps(params) if params else ""
                to_sign = timestamp + self.api_key + recv_window + payload_string
                signature = hmac.new(bytes(self.api_secret, "utf-8"), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

                headers = {
                    'X-BAPI-API-KEY': self.api_key,
                    'X-BAPI-TIMESTAMP': timestamp,
                    'X-BAPI-SIGN': signature,
                    'X-BAPI-RECV-WINDOW': recv_window,
                    'Content-Type': 'application/json'
                }
                
                response = self.session.post(self.base_url + endpoint, headers=headers, data=payload_string, timeout=15)

            response.raise_for_status()
            data = response.json()

            if data.get("retCode") != 0:
                raise BybitAPIError(ret_code=data.get("retCode"), ret_msg=data.get("retMsg"))
            
            return data.get("result", {})
            
        except RequestException as e:
            logger.error(f"Błąd sieciowy podczas komunikacji z Bybit. Endpoint: {endpoint}, Błąd: {e}")
            raise
        except BybitAPIError as e:
            logger.error(f"Błąd API Bybit. Endpoint: {endpoint}, Code: {e.ret_code}, Msg: '{e.ret_msg}'")
            raise

    def get_instrument_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        api_symbol = symbol.replace('.P', '')
        params = {"category": "linear", "symbol": api_symbol}
        try:
            result = self._send_request("GET", "/v5/market/instruments-info", params=params)
            if result and result.get('list'):
                instrument_data = result['list'][0]
                leverage_filter = instrument_data.get('leverageFilter', {})
                lot_size_filter = instrument_data.get('lotSizeFilter', {})
                price_filter = instrument_data.get('priceFilter', {})
                return {
                    "max_leverage": int(float(leverage_filter.get('maxLeverage', '1'))),
                    "qty_step": lot_size_filter.get('qtyStep', '0.001'),
                    "min_order_qty": float(lot_size_filter.get('minOrderQty', '0.0')),
                    "tick_size": price_filter.get('tickSize', '0.01')
                }
            return None
        except (RequestException, BybitAPIError):
            return None

    def place_limit_order(self, order_params: Dict[str, Any]) -> Optional[str]:
        symbol = order_params.get('symbol')
        if not symbol:
            logger.error("Brak 'symbol' w parametrach zlecenia.")
            return None
            
        api_symbol = symbol.replace('.P', '')
        side_map = {"LONG": "Buy", "SHORT": "Sell"}
        
        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "side": side_map[order_params['side']],
            "orderType": "Limit",
            "qty": str(order_params['qty']),
            "price": str(order_params['price']),
            "leverage": str(order_params['leverage']),
            "takeProfit": str(order_params['takeProfit']),
            "stopLoss": str(order_params['stopLoss']),
            "timeInForce": "GTC"
        }
        
        logger.info(f"[{symbol}] Wysyłanie zlecenia do Bybit z parametrami: {payload}")
        try:
            result = self._send_request("POST", "/v5/order/create", params=payload)
            order_id = result.get("orderId")
            if order_id:
                logger.info(f"[{symbol}] Zlecenie pomyślnie złożone. Order ID: {order_id}")
                return order_id
            
            logger.error(f"[{symbol}] API Bybit nie zwróciło orderId. Pełna odpowiedź 'result': {result}")
            return None
        except (RequestException, BybitAPIError) as e:
            logger.critical(
                f"[{symbol}] KRYTYCZNY BŁĄD podczas wywołania _send_request w place_limit_order. Błąd: {e}",
                exc_info=True
            )
            return None

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Anuluje aktywne zlecenie na podstawie jego ID."""
        api_symbol = symbol.replace('.P', '')
        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "orderId": order_id
        }
        logger.info(f"[{symbol}] Wysyłanie żądania anulowania zlecenia: {order_id}")
        try:
            result = self._send_request("POST", "/v5/order/cancel", params=payload)
            # Sprawdzamy, czy API zwróciło ID anulowanego zlecenia
            if result.get("orderId") == order_id:
                logger.info(f"[{symbol}] Zlecenie {order_id} pomyślnie anulowane.")
                return True
            else:
                logger.error(f"[{symbol}] API Bybit nie potwierdziło anulowania zlecenia {order_id}. Odpowiedź: {result}")
                return False
        except BybitAPIError as e:
            # Jeśli zlecenie już nie istnieje (bo np. zostało zrealizowane), traktujemy to jako sukces
            if e.ret_code == 110021: # Order does not exist or has been closed
                logger.warning(f"[{symbol}] Próba anulowania zlecenia {order_id}, które już nie istnieje (prawdopodobnie zrealizowane lub anulowane). Traktuję jako sukces.")
                return True
            logger.critical(f"[{symbol}] Błąd API Bybit podczas anulowania zlecenia {order_id}: {e}")
            return False
        except RequestException as e:
            logger.critical(f"[{symbol}] Błąd sieciowy podczas anulowania zlecenia {order_id}: {e}")
            return False


    def close_position_market(self, symbol: str, side: str) -> bool:
        """Zamyka pozycję dla danego symbolu przez złożenie zlecenia rynkowego w przeciwnym kierunku."""
        api_symbol = symbol.replace('.P', '')
        
        # Składamy zlecenie w przeciwnym kierunku, aby zamknąć pozycję
        close_side_map = {"LONG": "Sell", "SHORT": "Buy"}
        
        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "side": close_side_map[side.upper()],
            "orderType": "Market",
            "qty": "0",  # Qty "0" z closeOnTrigger=True zamyka całą pozycję
            "reduceOnly": True,
            "closeOnTrigger": True
        }
        logger.info(f"[{symbol}] Wysyłanie zlecenia rynkowego zamknięcia pozycji z parametrami: {payload}")
        try:
            result = self._send_request("POST", "/v5/order/create", params=payload)
            if result.get("orderId"):
                logger.info(f"[{symbol}] Zlecenie zamknięcia pozycji pomyślnie wysłane. Order ID: {result.get('orderId')}")
                return True
            return False
        except BybitAPIError as e:
            # Jeśli pozycja już nie istnieje, to nasz cel (zamknięcie) został osiągnięty
            if e.ret_code == 110025: # Position is not exists
                 logger.warning(f"[{symbol}] Próba zamknięcia pozycji, która już nie istnieje. Traktuję jako sukces.")
                 return True
            logger.critical(f"[{symbol}] Błąd API Bybit podczas zamykania pozycji: {e}")
            return False
        except RequestException as e:
            logger.critical(f"[{symbol}] Błąd sieciowy podczas zamykania pozycji: {e}")
            return False

    # Lokalizacja: bot_service/bybit_executor.py (dodać w klasie BybitExecutor)

    def get_order_status(self, symbol: str, order_id: str) -> Optional[str]:
        """
        Pobiera status konkretnego zlecenia z Bybit.
        Zwraca status jako string (np. "New", "Filled", "Cancelled") lub None w przypadku błędu.
        """
        api_symbol = symbol.replace('.P', '')
        params = {
            "category": "linear",
            "symbol": api_symbol,
            "orderId": order_id
        }
        logger.info(f"[{symbol}] Sprawdzanie statusu zlecenia {order_id}...")
        try:
            # Używamy endpointu do historii zleceń, bo pokazuje on wszystkie statusy
            result = self._send_request("GET", "/v5/order/history", params=params)
            if result and result.get('list'):
                order_data = result['list'][0]
                status = order_data.get("orderStatus")
                logger.info(f"[{symbol}] Status zlecenia {order_id} to: '{status}'")
                return status
            
            logger.warning(f"[{symbol}] Nie znaleziono zlecenia o ID {order_id} w historii. Może być bardzo stare lub niepoprawne ID.")
            return "NotFound" # Zwracamy specjalny status, jeśli nie znaleziono
            
        except BybitAPIError as e:
            # Jeśli zlecenie nie istnieje, to na pewno nie jest "wiszącym" zleceniem
            if e.ret_code == 110021: # Order does not exist
                logger.warning(f"[{symbol}] Zlecenie {order_id} nie istnieje wg API. Traktuję jako 'NotFound'.")
                return "NotFound"
            logger.error(f"[{symbol}] Błąd API Bybit podczas sprawdzania statusu zlecenia {order_id}: {e}")
            return None
        except RequestException as e:
            logger.error(f"[{symbol}] Błąd sieciowy podczas sprawdzania statusu zlecenia {order_id}: {e}")
            return None



    def cancel_all_open_orders_for_symbol(self, symbol: str) -> bool:
        """
        Anuluje WSZYSTKIE aktywne zlecenia (głównie Limit) dla danego symbolu za pomocą jednego wywołania API.
        """
        api_symbol = symbol.replace('.P', '')
        logger.warning(f"[{symbol}] Rozpoczynam procedurę czyszczenia: anulowanie oczekujących zleceń za pomocą 'cancel-all'.")
        try:
            payload = {"category": "linear", "symbol": api_symbol}
            result = self._send_request("POST", "/v5/order/cancel-all", params=payload)
            
            if 'list' in result:
                cancelled_orders = result.get('list', [])
                if cancelled_orders:
                    logger.info(f"[{symbol}] Pomyślnie wysłano polecenie anulowania dla {len(cancelled_orders)} zleceń.")
                else:
                    logger.info(f"[{symbol}] Brak otwartych zleceń do anulowania.")
                return True
            else:
                logger.error(f"[{symbol}] API Bybit nie zwróciło oczekiwanej listy po próbie anulowania zleceń. Odpowiedź: {result}")
                return False
        except BybitAPIError as e:
            if "Order does not exist" in e.ret_msg:
                 logger.info(f"[{symbol}] Brak otwartych zleceń do anulowania (API zwróciło 'Order does not exist').")
                 return True
            logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD API podczas czyszczenia otwartych zleceň: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.critical(f"[{symbol}] Nieoczekiwany błąd podczas czyszczenia otwartych zleceň: {e}", exc_info=True)
            return False


    def has_open_position(self, symbol: str) -> bool:
        """
        Sprawdza, czy istnieje jakakolwiek otwarta pozycja dla danego symbolu.
        Zwraca True, jeśli pozycja istnieje, w przeciwnym razie False.
        """
        api_symbol = symbol.replace('.P', '')
        endpoint = "/v5/position/list"
        params = {
            "category": "linear",
            "symbol": api_symbol
        }
        try:
            result = self._send_request("GET", endpoint, params=params)
            if result and result.get('list'):
                position_data = result['list'][0]
                # Pole 'size' jest stringiem. Jeśli jest większe od "0", pozycja istnieje.
                position_size = float(position_data.get("size", "0"))
                if position_size > 0:
                    logger.warning(f"[{symbol}] ZABEZPIECZENIE: Wykryto istniejącą pozycję o wielkości {position_size}. Blokuję nowe zlecenie.")
                    return True
            return False
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Błąd podczas sprawdzania otwartych pozycji: {e}")
            # W przypadku błędu API, dla bezpieczeństwa zakładamy, że pozycja może istnieć.
            return True

    def get_last_closed_pnl(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Pobiera dane o ostatniej zamkniętej pozycji dla danego symbolu.
        Zwraca słownik z kluczowymi danymi lub None w przypadku błędu.
        """
        api_symbol = symbol.replace('.P', '')
        endpoint = "/v5/position/closed-pnl"
        params = {
            "category": "linear",
            "symbol": api_symbol,
            "limit": 1  # Chcemy tylko ostatnią pozycję
        }
        logger.info(f"[{symbol}] Pobieranie danych o zrealizowanym P&L dla ostatniej zamkniętej pozycji...")
        try:
            result = self._send_request("GET", endpoint, params=params)
            if result and result.get('list'):
                pnl_data = result['list'][0]
                
                # Konwertujemy kluczowe dane na odpowiednie typy
                return {
                    "avg_entry_price": float(pnl_data.get("avgEntryPrice", 0.0)),
                    "avg_exit_price": float(pnl_data.get("avgExitPrice", 0.0)),
                    "closed_pnl": float(pnl_data.get("closedPnl", 0.0)),
                    "qty": float(pnl_data.get("qty", 0.0)),
                    "order_id": pnl_data.get("orderId"), # ID zlecenia wejścia
                    "updated_time": int(pnl_data.get("updatedTime", 0)), # Timestamp zamknięcia w ms
                }
            logger.warning(f"[{symbol}] Nie znaleziono historii zamkniętych pozycji w Bybit.")
            return None
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Błąd podczas pobierania zrealizowanego P&L: {e}")
            return None