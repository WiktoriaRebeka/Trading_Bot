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
        """Generyczna, bezpieczna metoda do wysyłania podpisanych żądań do API v5."""
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
                query_string = "" # GET params are not used for POST
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
            
            full_url = self.base_url + endpoint
            if query_string:
                full_url += "?" + query_string

            if method.upper() == 'GET':
                response = self.session.get(full_url, headers=headers, timeout=15)
            else: # POST
                response = self.session.post(full_url, headers=headers, data=payload_string, timeout=15)

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
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd w _send_request: {e}", exc_info=True)
            raise

    def get_instrument_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Pobiera zasady handlu dla danego symbolu (tickSize, qtyStep)."""
        api_symbol = symbol.replace('.P', '')
        params = {"category": "linear", "symbol": api_symbol}
        try:
            result = self._send_request("GET", "/v5/market/instruments-info", params=params)
            if result and result.get('list'):
                instrument_data = result['list'][0]
                lot_size_filter = instrument_data.get('lotSizeFilter', {})
                price_filter = instrument_data.get('priceFilter', {})
                return {
                    "qtyStep": lot_size_filter.get('qtyStep', '0.001'),
                    "tickSize": price_filter.get('tickSize', '0.01')
                }
            logger.warning(f"[{symbol}] Nie znaleziono danych instrumentu w odpowiedzi API.")
            return None
        except (RequestException, BybitAPIError):
            return None

    def place_limit_order(self, order_params: Dict[str, Any]) -> Optional[str]:
        """Składa zlecenie LIMIT z jednoczesnym ustawieniem TP/SL."""
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
            logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD podczas składania zlecenia. Błąd: {e}", exc_info=True)
            return None

    def cancel_all_open_orders_for_symbol(self, symbol: str) -> bool:
        """Anuluje WSZYSTKIE aktywne zlecenia dla danego symbolu."""
        api_symbol = symbol.replace('.P', '')
        logger.info(f"[{symbol}] Anulowanie wszystkich oczekujących zleceń...")
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
            if e.ret_code == 110021: # Order does not exist
                 logger.info(f"[{symbol}] Brak otwartych zleceń do anulowania (API zwróciło 'Order does not exist').")
                 return True
            logger.critical(f"[{symbol}] Błąd API podczas czyszczenia otwartych zleceń: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.critical(f"[{symbol}] Nieoczekiwany błąd podczas czyszczenia otwartych zleceń: {e}", exc_info=True)
            return False

    def has_open_position(self, symbol: str) -> bool:
        """Sprawdza, czy istnieje otwarta pozycja dla danego symbolu."""
        api_symbol = symbol.replace('.P', '')
        params = {"category": "linear", "symbol": api_symbol}
        try:
            result = self._send_request("GET", "/v5/position/list", params=params)
            if result and result.get('list'):
                position_data = result['list'][0]
                position_size = float(position_data.get("size", "0"))
                if position_size > 0:
                    logger.warning(f"[{symbol}] ZABEZPIECZENIE: Wykryto istniejącą pozycję o wielkości {position_size}. Blokuję nowe zlecenie.")
                    return True
            return False
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Błąd podczas sprawdzania otwartych pozycji: {e}")
            return True # Dla bezpieczeństwa, w razie błędu zakładamy, że pozycja istnieje

    def get_closed_pnl_history(self, start_time_ms: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Pobiera historię zamkniętych pozycji od określonego czasu."""
        endpoint = "/v5/position/closed-pnl"
        params = {
            "category": "linear",
            "startTime": start_time_ms,
            "limit": limit
        }
        logger.info(f"Pobieranie historii P&L od timestampu {start_time_ms}...")
        try:
            result = self._send_request("GET", endpoint, params=params)
            pnl_list = result.get('list', [])
            if pnl_list:
                logger.info(f"Pomyślnie pobrano {len(pnl_list)} rekordów P&L.")
            return list(reversed(pnl_list)) # Zwracamy od najstarszych do najnowszych
        except (RequestException, BybitAPIError) as e:
            logger.error(f"Błąd podczas pobierania historii P&L: {e}")
            return []