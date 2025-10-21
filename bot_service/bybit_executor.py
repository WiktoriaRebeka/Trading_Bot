# Lokalizacja: bot_service/bybit_executor.py

import logging
import time
import hmac
import hashlib
import json
from typing import Dict, Any, Optional, List
from urllib.parse import urlencode
import requests
from requests.exceptions import RequestException, JSONDecodeError

logger = logging.getLogger(__name__)

class BybitAPIError(Exception):
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
                query_string = ""
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
            
            if not response.text:
                logger.warning(f"Otrzymano pustą odpowiedź od Bybit dla endpointu {endpoint}. Traktuję jako brak danych.")
                return {}
            
            data = response.json()
            # Usunięto zbyt szczegółowe logowanie pełnej odpowiedzi, aby nie zaśmiecać logów
            # logger.info(f"Pełna surowa odpowiedź z Bybit dla {endpoint}: {data}")

            if data.get("retCode") != 0:
                raise BybitAPIError(ret_code=data.get("retCode"), ret_msg=data.get("retMsg"))
            
            return data.get("result", {})
            
        except JSONDecodeError:
            logger.error(f"Błąd dekodowania JSON z Bybit dla endpointu {endpoint}. Odpowiedź nie była poprawnym JSON-em. Treść: {response.text}")
            raise
        except RequestException as e:
            logger.error(f"Błąd sieciowy podczas komunikacji z Bybit. Endpoint: {endpoint}, Błąd: {e}")
            raise
        except BybitAPIError as e:
            # Logujemy jako warning, bo wiele błędów API jest spodziewanych (np. brak zleceń do anulowania)
            logger.warning(f"Błąd API Bybit. Endpoint: {endpoint}, Code: {e.ret_code}, Msg: '{e.ret_msg}'")
            raise
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd w _send_request: {e}", exc_info=True)
            raise

    def place_order(self, params: Dict[str, Any]) -> Optional[Dict[str, str]]:
        symbol = params.get('symbol')
        if not symbol:
            logger.error("Brak 'symbol' w parametrach zlecenia.")
            return None

        api_symbol = symbol.replace('.P', '')
        
        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "side": params['side'],
            "orderType": params['orderType'],
            "qty": str(params['qty']),
        }

        # Lista wszystkich możliwych parametrów, które chcemy przekazać
        optional_params = [
            "price", "takeProfit", "stopLoss", "tpTriggerBy", "slTriggerBy", "orderLinkId", "timeInForce"
        ]

        for param in optional_params:
            if param in params:
                payload[param] = str(params[param])

        logger.info(f"[{symbol}] Wysyłanie zlecenia do Bybit: {payload}")
        try:
            result = self._send_request("POST", "/v5/order/create", params=payload)
            logger.info(f"[{symbol}] Odpowiedź Bybit na place_order: {result}")
            
            order_id = result.get("orderId")
            if order_id:
                return result
            
            logger.error(f"[{symbol}] API Bybit nie zwróciło orderId. Pełna odpowiedź 'result': {result}")
            return None
        except BybitAPIError as e:
            # Przekazujemy wyjątek dalej, aby logika biznesowa mogła na niego zareagować
            raise
        except Exception as e:
            logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD podczas składania zlecenia. Błąd: {e}", exc_info=True)
            raise

 

    def get_closed_pnl_history(self, start_time_ms: int, limit: int = 50) -> List[Dict[str, Any]]:
        endpoint = "/v5/position/closed-pnl"
        all_pnl_records = []
        cursor = None
        
        logger.info(f"Pobieranie historii P&L (Trade/SL/TP) od timestampu {start_time_ms}...")

        while True:
            params = {
                "category": "linear",
                "startTime": start_time_ms,
                "limit": limit
            }
            if cursor:
                params["cursor"] = cursor

            try:
                result = self._send_request("GET", endpoint, params=params)
                pnl_list = result.get('list', [])
                if pnl_list:
                    all_pnl_records.extend(pnl_list)
                cursor = result.get('nextPageCursor')
                if not cursor:
                    break
            except (RequestException, BybitAPIError) as e:
                logger.error(f"Błąd podczas pobierania strony historii P&L: {e}.")
                break
        
        logger.info(f"Pobrano {len(all_pnl_records)} rekordów P&L.")
        return list(reversed(all_pnl_records))

    def get_liquidation_history(self, start_time_ms: int, limit: int = 50) -> List[Dict[str, Any]]:
        endpoint = "/v5/asset/delivery-record"
        all_liq_records = []
        cursor = None
        
        logger.info(f"Pobieranie historii LIKWIDACJI od timestampu {start_time_ms}...")

        while True:
            params = {
                "category": "linear",
                "type": "LIQUIDATION",
                "startTime": start_time_ms,
                "limit": limit
            }
            if cursor:
                params["cursor"] = cursor

            try:
                result = self._send_request("GET", endpoint, params=params)
                liq_list = result.get('list', [])
                if liq_list:
                    all_liq_records.extend(liq_list)
                cursor = result.get('nextPageCursor')
                if not cursor:
                    break
            except (RequestException, BybitAPIError) as e:
                logger.error(f"Błąd podczas pobierania strony historii likwidacji: {e}.")
                break
        
        logger.info(f"Pobrano {len(all_liq_records)} rekordów likwidacji.")
        return list(reversed(all_liq_records))

    def get_open_position_side(self, symbol: str) -> Optional[str]:
        api_symbol = symbol.replace('.P', '')
        params = {"category": "linear", "symbol": api_symbol}
        try:
            result = self._send_request("GET", "/v5/position/list", params=params)
            if result and result.get('list'):
                position_data = result['list'][0]
                position_size = float(position_data.get("size", "0"))
                if position_size > 0:
                    side = position_data.get("side")
                    if side == "Buy":
                        return "LONG"
                    elif side == "Sell":
                        return "SHORT"
            return None
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Błąd podczas sprawdzania otwartych pozycji: {e}")
            return "ERROR"

    def cancel_all_open_orders_for_symbol(self, symbol: str) -> bool:
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
            if e.ret_code == 110021:
                 logger.info(f"[{symbol}] Brak otwartych zleceń do anulowania (API zwróciło 'Order does not exist').")
                 return True
            logger.critical(f"[{symbol}] Błąd API podczas czyszczenia otwartych zleceń: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.critical(f"[{symbol}] Nieoczekiwany błąd podczas czyszczenia otwartych zleceń: {e}", exc_info=True)
            return False

    def get_latest_ticker_price(self, symbol: str) -> Optional[float]:
        api_symbol = symbol.replace('.P', '')
        params = {"category": "linear", "symbol": api_symbol}
        try:
            result = self._send_request("GET", "/v5/market/tickers", params=params)
            if result and result.get('list'):
                ticker_data = result['list'][0]
                last_price = ticker_data.get('lastPrice')
                if last_price:
                    return float(last_price)
            logger.warning(f"[{symbol}] Nie znaleziono 'lastPrice' w odpowiedzi z endpointu /tickers.")
            return None
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Błąd podczas pobierania aktualnej ceny rynkowej: {e}")
            return None

    def get_instrument_info(self, symbol: str) -> Optional[Dict[str, Any]]:
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

    def place_conditional_order(self, params: Dict[str, Any]) -> Optional[Dict[str, str]]:
        symbol = params.get('symbol')
        logger.info(f"[{symbol}] Składanie zlecenia warunkowego: {params}")
        try:
            result = self._send_request("POST", "/v5/order/create", params=params)
            order_id = result.get("orderId")
            if order_id:
                logger.info(f"[{symbol}] Zlecenie warunkowe pomyślnie złożone. Order ID: {order_id}")
                return {"orderId": order_id}
            logger.error(f"[{symbol}] API Bybit nie zwróciło orderId dla zlecenia warunkowego. Odpowiedź: {result}")
            return None
        except (RequestException, BybitAPIError) as e:
            logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD podczas składania zlecenia warunkowego. Błąd: {e}", exc_info=True)
            return None

    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        params = {"category": "linear", "orderId": order_id}
        try:
            result = self._send_request("GET", "/v5/order/realtime", params=params)
            if result and result.get('list'):
                return result['list'][0]
            return None
        except (RequestException, BybitAPIError):
            return None

    def close_position_market(self, symbol: str, qty: str, side: str):
        api_symbol = symbol.replace('.P', '')
        payload = {
            "category": "linear", "symbol": api_symbol,
            "side": side, "orderType": "Market", "qty": qty,
            "reduceOnly": True
        }
        logger.warning(f"[{symbol}] AWARYJNE ZAMYKANIE POZYCJI zleceniem MARKET: {payload}")
        try:
            self._send_request("POST", "/v5/order/create", params=payload)
        except Exception as e:
            logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD podczas awaryjnego zamykania pozycji: {e}", exc_info=True)