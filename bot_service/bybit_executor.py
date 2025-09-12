# Lokalizacja: bot_service/bybit_executor.py
import logging
import time
import hmac
import hashlib
import json
from typing import Dict, Any, Optional, List
from urllib.parse import urlencode
import requests
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)

class BybitAPIError(Exception):
    """Niestandardowy wyjątek dla błędów zwracanych przez API Bybit."""
    def __init__(self, ret_code: int, ret_msg: str):
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        super().__init__(f"Bybit API Error: [Code: {ret_code}] {ret_msg}")

class BybitExecutor:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        if not api_key or not api_secret:
            raise ValueError("Klucze API Bybit nie mogą być puste.")
        
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api-demo.bybit.com" if testnet else "https://api.bybit.com"
        self.session = requests.Session()
        logger.info(f"BybitExecutor zainicjalizowany. Tryb Testnet: {testnet}. URL: {self.base_url}")

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
            else: # POST
                payload_string = json.dumps(params) if params else ""
                to_sign = timestamp + self.api_key + recv_window + payload_string

            signature = hmac.new(bytes(self.api_secret, "utf-8"), to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
            
            headers = {'X-BAPI-API-KEY': self.api_key, 'X-BAPI-TIMESTAMP': timestamp, 'X-BAPI-SIGN': signature, 'X-BAPI-RECV-WINDOW': recv_window, 'Content-Type': 'application/json'}
            
            full_url = self.base_url + endpoint
            if query_string and method.upper() == 'GET':
                full_url += "?" + query_string

            response = self.session.request(method, full_url, headers=headers, data=(payload_string if method.upper() == 'POST' else None), params=(params if method.upper() == 'GET' else None), timeout=15)
            response.raise_for_status()
            data = response.json()

            # Logowanie pełnej odpowiedzi dla celów debugowania
            logger.debug(f"Odpowiedź API z {endpoint}: {data}")

            if data.get("retCode") != 0:
                raise BybitAPIError(ret_code=data.get("retCode"), ret_msg=data.get("retMsg"))
            
            return data.get("result", {})
            
        except RequestException as e:
            logger.error(f"Błąd sieciowy podczas komunikacji z Bybit. Endpoint: {endpoint}, Błąd: {e}")
            raise
        except BybitAPIError as e:
            logger.error(f"Błąd API Bybit. Endpoint: {endpoint}, Code: {e.ret_code}, Msg: '{e.ret_msg}'")
            raise
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd w _send_request: {e}", exc_info=True)
            raise

    def get_instrument_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        api_symbol = symbol.replace('.P', '')
        params = {"category": "linear", "symbol": api_symbol}
        try:
            result = self._send_request("GET", "/v5/market/instruments-info", params=params)
            if result and result.get('list'):
                instrument_data = result['list'][0]
                return {"qtyStep": instrument_data.get('lotSizeFilter', {}).get('qtyStep'), "tickSize": instrument_data.get('priceFilter', {}).get('tickSize')}
            return None
        except (RequestException, BybitAPIError):
            return None

    def place_limit_order(self, order_params: Dict[str, Any]) -> Optional[Dict[str, str]]:
        symbol = order_params.get('symbol')
        api_symbol = symbol.replace('.P', '')
        side_map = {"LONG": "Buy", "SHORT": "Sell"}
        payload = {"category": "linear", "symbol": api_symbol, "side": side_map[order_params['side']], "orderType": "Limit", "qty": str(order_params['qty']), "price": str(order_params['price']), "orderLinkId": order_params.get('orderLinkId'), "timeInForce": "GTC"}
        logger.info(f"[{symbol}] Wysyłanie zlecenia WEJŚCIOWEGO: {payload}")
        try:
            result = self._send_request("POST", "/v5/order/create", params=payload)
            logger.info(f"[{symbol}] Odpowiedź Bybit na zlecenie wejściowe: {result}")
            return result
        except (RequestException, BybitAPIError) as e:
            logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD podczas składania zlecenia wejściowego: {e}", exc_info=True)
            return None

    def get_order_status(self, symbol: str, order_id: Optional[str] = None, order_link_id: Optional[str] = None) -> Dict[str, Any]:
        api_symbol = symbol.replace('.P', '')
        params = {"category": "linear", "symbol": api_symbol}
        if order_id:
            params['orderId'] = order_id
        elif order_link_id:
            params['orderLinkId'] = order_link_id
        else:
            raise ValueError("Należy podać orderId lub orderLinkId")
        
        result = self._send_request("GET", "/v5/order/history", params=params)
        if result and result.get('list'):
            return result['list'][0]
        return {}

    def set_trading_stop(self, symbol: str, stop_loss: float, take_profit: float, trigger_by: str = "LastPrice") -> Optional[Dict[str, Any]]:
        api_symbol = symbol.replace('.P', '')
        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "stopLoss": str(stop_loss),
            "takeProfit": str(take_profit),
            "tpslMode": "Full",
            "triggerBy": trigger_by
        }
        logger.info(f"[{symbol}] Ustawiam trading-stop (TP/SL) dla otwartej pozycji: {payload}")
        try:
            result = self._send_request("POST", "/v5/position/trading-stop", params=payload)
            logger.info(f"[{symbol}] Odpowiedź Bybit na set_trading_stop: {result}")
            return result
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Błąd przy ustawianiu trading-stop: {e}")
            return None

    def cancel_all_open_orders_for_symbol(self, symbol: str) -> bool:
        api_symbol = symbol.replace('.P', '')
        logger.info(f"[{symbol}] Anulowanie wszystkich oczekujących zleceń...")
        try:
            self._send_request("POST", "/v5/order/cancel-all", params={"category": "linear", "symbol": api_symbol})
            return True
        except BybitAPIError as e:
            if e.ret_code == 110021: return True
            logger.critical(f"[{symbol}] Błąd API podczas czyszczenia zleceń: {e}")
            return False
        except Exception as e:
            logger.critical(f"[{symbol}] Nieoczekiwany błąd podczas czyszczenia zleceń: {e}")
            return False

    def has_open_position(self, symbol: str) -> bool:
        api_symbol = symbol.replace('.P', '')
        try:
            result = self._send_request("GET", "/v5/position/list", params={"category": "linear", "symbol": api_symbol})
            if result and result.get('list') and float(result['list'][0].get("size", "0")) > 0:
                return True
            return False
        except (RequestException, BybitAPIError):
            return True # Fail-safe

    def get_closed_pnl_history(self, start_time_ms: int, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            result = self._send_request("GET", "/v5/position/closed-pnl", params={"category": "linear", "startTime": start_time_ms, "limit": limit})
            return list(reversed(result.get('list', [])))
        except (RequestException, BybitAPIError):
            return []

    def close_position_market(self, symbol: str, side: str) -> bool:
        api_symbol = symbol.replace('.P', '')
        try:
            # Pobierz aktualny rozmiar pozycji, aby zamknąć właściwą ilość
            pos_result = self._send_request("GET", "/v5/position/list", params={"category": "linear", "symbol": api_symbol})
            if not (pos_result and pos_result.get('list')):
                logger.warning(f"[{symbol}] Próba awaryjnego zamknięcia, ale nie znaleziono otwartej pozycji.")
                return True
            
            size = pos_result['list'][0].get("size", "0")
            if float(size) == 0:
                logger.warning(f"[{symbol}] Próba awaryjnego zamknięcia, ale rozmiar pozycji wynosi 0.")
                return True

            close_side = "Sell" if side == "LONG" else "Buy"
            payload = {"category": "linear", "symbol": api_symbol, "side": close_side, "orderType": "Market", "qty": size, "reduceOnly": True}
            logger.warning(f"[{symbol}] AWARIA: Zamykanie pozycji zleceniem MARKET: {payload}")
            self._send_request("POST", "/v5/order/create", params=payload)
            return True
        except Exception as e:
            logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD podczas awaryjnego zamykania pozycji: {e}", exc_info=True)
            return False