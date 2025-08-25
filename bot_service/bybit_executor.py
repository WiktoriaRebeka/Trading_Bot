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