# Lokalizacja: bot_service/bybit_executor.py

import logging
from typing import Dict, Any, Optional, Set
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

from shared_lib import constants

logger = logging.getLogger(__name__)

bigquery_client: Optional[bigquery.Client] = None
TABLE_REF: Optional[str] = None

class BybitAPIError(Exception):
    def __init__(self, ret_code: int, ret_msg: str):
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        super().__init__(f"Bybit API Error: [Code: {ret_code}] {ret_msg}")

class BybitExecutor:
    def __init__(self, api_key: str, api_secret: str):
        if not api_key or not api_secret:
            raise ValueError("Klucze API Bybit nie mogą być puste.")
        
        self.api_key: str = api_key
        self.api_secret: str = api_secret
        self.base_url: str = constants.BYBIT_API_URL_V5
        self.session = requests.Session()

    def _send_request(self, method: str, endpoint: str, params: Dict = None, payload: Dict = None) -> Dict[str, Any]:
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
            response = self.session.request(method, full_url, headers=headers, params=params, data=body_data, timeout=15)
            response.raise_for_status()
            data = response.json()

            if data.get("retCode") != 0:
                logger.error(
                    f"Bybit API zwróciło błąd. Endpoint: {endpoint}, "
                    f"retCode: {data.get('retCode')}, retMsg: '{data.get('retMsg')}', "
                    f"Pełna odpowiedź: {data}"
                )
                raise BybitAPIError(ret_code=data.get("retCode"), ret_msg=data.get("retMsg"))
            
            return data.get("result", {})
        except RequestException as e:
            error_content = e.response.text if e.response else "Brak odpowiedzi od serwera (prawdopodobnie timeout)."
            logger.error(
                f"Błąd sieciowy podczas komunikacji z Bybit. Endpoint: {endpoint}, "
                f"Typ błędu: {type(e).__name__}, Błąd: {e}. "
                f"Odpowiedź serwera: {error_content}"
            )
            raise
        except BybitAPIError:
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
            return {}
        except (RequestException, BybitAPIError):
            return None
        
    def place_limit_order(self, order_params: Dict[str, Any]) -> Optional[str]:
        symbol = order_params.get('symbol')
        if not symbol:
            logger.error("Brak 'symbol' w parametrach zlecenia.")
            return None

        api_symbol = symbol.replace('.P', '')
        
        side_map = {"LONG": "Buy", "SHORT": "Sell"}
        side_value = str(order_params.get('side', '')).upper()
        
        if side_value not in side_map:
            logger.error(f"[{symbol}] Nieprawidłowa wartość 'side': {order_params.get('side')}. Oczekiwano 'LONG' lub 'SHORT'.")
            return None

        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "side": side_map[side_value],
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
            result = self._send_request("POST", "/v5/order/create", payload=payload)
            order_id = result.get("orderId")
            if order_id:
                logger.info(f"[{symbol}] Zlecenie pomyślnie złożone. Order ID: {order_id}")
                return order_id
            else:
                logger.error(f"[{symbol}] API Bybit nie zwróciło orderId, chociaż nie było wyjątku. Odpowiedź: {result}")
                return None
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Nie udało się złożyć zlecenia z powodu błędu API: {e}")
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