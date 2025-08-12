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
        # === POPRAWKA 1: Upewniamy się, że base_url jest poprawny ===
        self.base_url: str = "https://api.bybit.com"
        self.session = requests.Session()

    def _send_request(self, method: str, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        OSTATECZNA POPRAWIONA WERSJA.
        Używa `json=` dla zapytań POST dla większej niezawodności.
        """
        timestamp = str(int(time.time() * 1000))
        recv_window = "10000"
        
        if params:
            params = dict(sorted(params.items()))
        
        if method.upper() == 'GET':
            query_string = urlencode(params) if params else ""
            payload_string = ""
        else: # POST
            query_string = ""
            payload_string = json.dumps(params) if params else ""

        to_sign = timestamp + self.api_key + recv_window + query_string + payload_string
        signature = hmac.new(bytes(self.api_secret, "utf-8"), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        
        headers = {
            'X-B-API-KEY': self.api_key,
            'X-B-API-TIMESTAMP': timestamp,
            'X-B-API-SIGN': signature,
            'X-B-API-RECV-WINDOW': recv_window,
            'Content-Type': 'application/json',
        }
        
        full_url = self.base_url + endpoint
        
        try:
            if method.upper() == 'GET':
                response = self.session.get(full_url, headers=headers, params=params, timeout=15)
            else: # POST
                # === POPRAWKA 2: Używamy `json=params` zamiast `data=payload_string` ===
                response = self.session.post(full_url, headers=headers, json=params, timeout=15)
            
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
                return {
                    "max_leverage": int(float(leverage_filter.get('maxLeverage', '1'))),
                    "qty_step": lot_size_filter.get('qtyStep', '0.001')
                }
            return None
        except (RequestException, BybitAPIError):
            return None
        
    def place_limit_order(self, order_params: Dict[str, Any]) -> Optional[str]:
        symbol = order_params.get('symbol')
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
            logger.error(f"[{symbol}] API Bybit nie zwróciło orderId. Odpowiedź: {result}")
            return None
        except (RequestException, BybitAPIError):
            return None