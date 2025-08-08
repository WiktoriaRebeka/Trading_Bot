# Lokalizacja: bot_service/bybit_executor.py

import logging
import time
import hmac
import hashlib
import json
from typing import Optional, Dict, Any
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode

import requests
from requests.exceptions import RequestException

from shared_lib.config import config
from shared_lib import constants

logger = logging.getLogger(__name__)

class BybitAPIError(Exception):
    def __init__(self, ret_code: int, ret_msg: str):
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        super().__init__(f"Bybit API Error: [Code: {ret_code}] {ret_msg}")

class BybitExecutor:
    def __init__(self):
        if not config.is_loaded:
            raise RuntimeError("Konfiguracja (config) nie została załadowana.")
        
        self.api_key: str = config.BYBIT_API_KEY
        self.api_secret: str = config.BYBIT_API_SECRET
        self.base_url: str = constants.BYBIT_API_URL_V5
        self.session = requests.Session()

        if not self.api_key or not self.api_secret:
            raise ValueError("Klucze API Bybit nie są ustawione w konfiguracji.")

    def _send_request(self, method: str, endpoint: str, params: Dict = None, payload: Dict = None) -> Dict[str, Any]:
        """
        Wysyła podpisane zapytanie do API Bybit V5.
        Uproszczona i zweryfikowana wersja.
        """
        full_url = self.base_url + endpoint
        timestamp = str(int(time.time() * 1000))
        recv_window = "10000" 
        
        if method.upper() == 'GET':
            param_str = urlencode(sorted(params.items())) if params else ""
            body_data = None
        else: 
            param_str = json.dumps(payload) if payload else ""
            body_data = param_str 

       
        to_sign = timestamp + self.api_key + recv_window + param_str
        signature = hmac.new(bytes(self.api_secret, "utf-8"), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        
        headers = {
            'X-B-API-KEY': self.api_key,
            'X-B-API-TIMESTAMP': timestamp,
            'X-B-API-SIGN': signature,
            'X-B-API-RECV-WINDOW': recv_window,
        }
        
        if method.upper() != 'GET':
            headers['Content-Type'] = 'application/json'
        
        try:
            # Użycie requests.request jest bardziej elastyczne
            response = self.session.request(method, full_url, headers=headers, params=params, data=body_data, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("retCode") != 0:
                # Teraz to zadziała, bo BybitAPIError ma poprawny __init__
                raise BybitAPIError(ret_code=data.get("retCode"), ret_msg=data.get("retMsg"))
            
            return data.get("result", {})
        except RequestException as e:
            error_content = e.response.text if e.response else "No response content"
            logger.error(f"Błąd sieciowy podczas komunikacji z Bybit: {e}. Odpowiedź serwera: {error_content}")
            raise
        except BybitAPIError as e:
            logger.error(f"Błąd API Bybit: {e}", extra={"json_fields": {"ret_code": e.ret_code, "ret_msg": e.ret_msg}})
            raise
        except Exception as e:
            logger.critical(f"Nieoczekiwany błąd w _send_request: {e}", exc_info=True)
            raise

    def get_instrument_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        logger.info(f"[{symbol}] Pobieranie informacji o instrumencie z Bybit.")
        api_symbol = symbol.replace('.P', '')
        try:
            result = self._send_request(
                "GET",
                "/v5/market/instruments-info",
                params={"category": "linear", "symbol": api_symbol}
            )
            if result and result.get('list'):
                instrument_data = result['list'][0]
                leverage_filter = instrument_data.get('leverageFilter', {})
                lot_size_filter = instrument_data.get('lotSizeFilter', {})
                info = {
                    "max_leverage": int(float(leverage_filter.get('maxLeverage', '1'))),
                    "qty_step": lot_size_filter.get('qtyStep', '0.001')
                }
                return info
            return None
        except (RequestException, BybitAPIError):
            return None

    def get_position_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        logger.info(f"[{symbol}] Pobieranie informacji o pozycji z Bybit.")
        api_symbol = symbol.replace('.P', '')
        try:
            result = self._send_request(
                "GET",
                "/v5/position/list",
                params={"category": "linear", "symbol": api_symbol}
            )
            if result and result.get('list') and len(result['list']) > 0:
                return result['list'][0]
            return None
        except (RequestException, BybitAPIError):
            return None
        
    def place_limit_order(self, order_params: Dict[str, Any]) -> Optional[str]:
        symbol = order_params.get('symbol')
        api_symbol = symbol.replace('.P', '')
        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "side": "Buy" if str(order_params['side']).upper() == 'LONG' else "Sell",
            "orderType": "Limit",
            "qty": str(order_params['qty']),
            "price": str(order_params['price']),
            "leverage": str(order_params['leverage']),
            "takeProfit": str(order_params['takeProfit']),
            "stopLoss": str(order_params['stopLoss']),
            "timeInForce": "GTC"
        }
        try:
            result = self._send_request("POST", "/v5/order/create", payload=payload)
            return result.get("orderId")
        except (RequestException, BybitAPIError):
            return None

    def set_isolated_margin(self, symbol: str, leverage: int) -> bool:
        logger.info(f"[{symbol}] Próba ustawienia trybu Isolated Margin z dźwignią {leverage}x.")
        api_symbol = symbol.replace('.P', '')
        leverage_str = str(leverage)
        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "buyLeverage": leverage_str,
            "sellLeverage": leverage_str,
            "tradeMode": 1
        }
        try:
            self._send_request("POST", "/v5/position/set-leverage", payload=payload)
            return True
        except (RequestException, BybitAPIError) as e:
            if isinstance(e, BybitAPIError) and e.ret_code == 110043:
                logger.warning(f"[{symbol}] Dźwignia i tryb margin są już poprawnie ustawione.")
                return True
            logger.error(f"[{symbol}] KRYTYCZNY BŁĄD: Nie udało się ustawić trybu Isolated Margin: {e}")
            return False

def format_quantity(quantity: float, qty_step: str) -> str:
    qty_decimal = Decimal(str(quantity))
    step_decimal = Decimal(qty_step)
    formatted_qty = qty_decimal.quantize(step_decimal, rounding=ROUND_DOWN)
    return str(formatted_qty)