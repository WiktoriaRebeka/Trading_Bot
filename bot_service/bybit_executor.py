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
            headers = {'X-BAPI-API-KEY': self.api_key, 'X-BAPI-TIMESTAMP': timestamp, 'X-BAPI-SIGN': signature, 'X-BAPI-RECV-WINDOW': recv_window, 'Content-Type': 'application/json'}
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
            # --- NOWE, TWARDE ZABEZPIECZENIE ---
        # Sprawdzamy, czy zlecenie jest typu LIMIT i czy ZAWSZE zawiera SL i TP.
        if params.get("orderType") == "Limit":
            if not params.get("stopLoss") or not params.get("takeProfit"):
                logger.critical(
                    f"[{symbol}] KRYTYCZNA PRÓBA WYSŁANIA ZLECENIA LIMIT BEZ SL/TP! Zlecenie zablokowane. Parametry: {params}"
                )
                # Zwracamy None, aby zablokować wysłanie zlecenia
                return None
        # --- KONIEC ZABEZPIECZENIA ---
        api_symbol = symbol.replace('.P', '')
        payload = {"category": "linear", "symbol": api_symbol, "side": params['side'], "orderType": params['orderType'], "qty": str(params['qty'])}
        optional_params = ["price", "takeProfit", "stopLoss", "tpTriggerBy", "slTriggerBy", "orderLinkId", "timeInForce"]
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
            raise
        except Exception as e:
            logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD podczas składania zlecenia. Błąd: {e}", exc_info=True)
            raise

    def get_closed_pnl_history(self, start_time_ms: int, limit: int = 100) -> List[Dict[str, Any]]:
        endpoint = "/v5/position/closed-pnl"
        all_pnl_records = []
        cursor = None
        logger.info(f"Pobieranie historii P&L (Trade/SL/TP) od timestampu {start_time_ms}...")
        while True:
            params = {"category": "linear", "startTime": start_time_ms, "limit": limit}
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
                    if side == "Buy": return "LONG"
                    elif side == "Sell": return "SHORT"
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
            logger.critical(f"[{symbol}] Błąd API podczas czyszczenia otwartych zleceň: {e}", exc_info=False)
            return False
        except Exception as e:
            logger.critical(f"[{symbol}] Nieoczekiwany błąd podczas czyszczenia otwartych zleceň: {e}", exc_info=True)
            return False

    def get_order_history_by_id(self, order_id: str = None, order_link_id: str = None) -> Optional[Dict[str, Any]]:
        """
        Pobiera szczegóły historycznego zlecenia na podstawie jego orderId LUB orderLinkId.
        """
        if not order_id and not order_link_id:
            return None
            
        endpoint = "/v5/order/history"
        
        # Wyszukiwanie po orderLinkId jest bardziej niezawodne
        if order_link_id:
            params = {"category": "linear", "orderLinkId": order_link_id}
            try:
                result = self._send_request("GET", endpoint, params=params)
                if result and result.get('list'):
                    logger.info(f"Znaleziono historię zlecenia po orderLinkId {order_link_id}.")
                    return result['list'][0]
            except Exception:
                logger.warning(f"Nie udało się sprawdzić historii po orderLinkId {order_link_id}.")

        if order_id:
            params = {"category": "linear", "orderId": order_id, "orderFilter": "StopOrder"}
            try:
                result = self._send_request("GET", endpoint, params=params)
                if result and result.get('list'):
                    logger.info(f"Znaleziono orderId {order_id} w historii zleceň warunkowych.")
                    return result['list'][0]
            except Exception:
                logger.warning(f"Nie udało się sprawdzić historii zleceň warunkowych dla {order_id}.")

            params = {"category": "linear", "orderId": order_id}
            try:
                result = self._send_request("GET", endpoint, params=params)
                if result and result.get('list'):
                    logger.info(f"Znaleziono orderId {order_id} w historii zleceň zwykłych.")
                    return result['list'][0]
            except Exception:
                logger.warning(f"Nie udało się sprawdzić historii zleceň zwykłych dla {order_id}.")

        logger.warning(f"Ostatecznie nie znaleziono historii dla orderId: {order_id} / orderLinkId: {order_link_id}.")
        return None

    def get_open_order_by_id(self, order_id: str = None, order_link_id: str = None) -> Optional[Dict[str, Any]]:
        """
        Pobiera szczegóły AKTYWNEGO, OTWARTEGO zlecenia na podstawie jego
        orderId LUB orderLinkId.
        """
        if not order_id and not order_link_id:
            return None

        endpoint = "/v5/order/realtime"
        params = {"category": "linear"}
        if order_link_id:
            params["orderLinkId"] = order_link_id
        else:
            params["orderId"] = order_id
            
        try:
            result = self._send_request("GET", endpoint, params=params)
            if result and result.get('list'):
                logger.info(f"Znaleziono aktywne zlecenie dla orderId: {order_id} / orderLinkId: {order_link_id}.")
                return result['list'][0]
            return None
        except Exception:
            logger.warning(f"Nie udało się sprawdzić aktywnych zleceň dla orderId: {order_id} / orderLinkId: {order_link_id}.")
            return None

    def get_active_tp_sl_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """Pobiera listę aktywnych zleceň warunkowych (TP/SL) dla danego symbolu."""
        api_symbol = symbol.replace('.P', '')
        endpoint = "/v5/order/realtime"
        params = {
            "category": "linear",
            "symbol": api_symbol,
            "orderFilter": "StopOrder"
        }
        try:
            result = self._send_request("GET", endpoint, params=params)
            order_list = result.get('list', [])
            logger.info(f"[{symbol}] Znaleziono {len(order_list)} aktywnych zleceň TP/SL.")
            return order_list
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Błąd podczas pobierania aktywnych zleceň TP/SL: {e}")
            return []
    def get_mark_price(self, symbol: str) -> Optional[float]:
        """Pobiera aktualną cenę rynkową (Mark Price) dla danego symbolu."""
        api_symbol = symbol.replace('.P', '')
        params = {"category": "linear", "symbol": api_symbol}
        try:
            result = self._send_request("GET", "/v5/market/tickers", params=params)
            if result and result.get('list'):
                return float(result['list'][0].get('markPrice'))
            return None
        except Exception as e:
            logger.error(f"[{api_symbol}] Błąd podczas pobierania Mark Price: {e}")
            return None



    def cancel_order(self, symbol: str, order_id: str = None, order_link_id: str = None) -> bool:
        """Anuluje pojedyncze zlecenie na podstawie jego ID lub Link ID."""
        api_symbol = symbol.replace('.P', '')
        payload = {"category": "linear", "symbol": api_symbol}
        if order_id:
            payload['orderId'] = order_id
        elif order_link_id:
            payload['orderLinkId'] = order_link_id
        else:
            logger.error(f"[{api_symbol}] Musisz podać orderId lub orderLinkId do anulowania zlecenia.")
            return False

        logger.info(f"[{api_symbol}] Wysyłanie polecenia anulowania dla zlecenia: {payload}")
        try:
            self._send_request("POST", "/v5/order/cancel", params=payload)
            logger.info(f"[{api_symbol}] Polecenie anulowania wysłane pomyślnie.")
            return True
        except BybitAPIError as e:
            # --- KLUCZOWA ZMIANA ---
            # Jeśli zlecenie już nie istnieje (bo zostało zrealizowane), traktujemy to jako sukces.
            if e.ret_code == 110001: # Order does not exist or too late to cancel
                logger.warning(f"[{api_symbol}] Próba anulowania zlecenia, które już nie istnieje (prawdopodobnie zrealizowane). Traktuję jako sukces.")
                return True
            # --- KONIEC ZMIANY ---
            logger.error(f"[{api_symbol}] Błąd API podczas anulowania zlecenia: {e}")
            return False
        except Exception as e:
            logger.critical(f"[{api_symbol}] KRYTYCZNY BŁĄD podczas anulowania zlecenia: {e}", exc_info=True)
            return False

    def set_trailing_stop(self, symbol: str, trailing_stop_price: str, sl_price: str) -> bool:
        """Ustawia lub modyfikuje Trailing Stop dla otwartej pozycji."""
        api_symbol = symbol.replace('.P', '')
        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "tpslMode": "Partial",
            "trailingStop": trailing_stop_price,
            "stopLoss": sl_price
        }
        logger.info(f"[{api_symbol}] Ustawianie Trailing Stop: {payload}")
        try:
            # --- POPRAWIONA NAZWA ENDPOINTU ---
            self._send_request("POST", "/v5/position/trading-stop", params=payload)
            logger.info(f"[{api_symbol}] Trailing Stop pomyślnie ustawiony.")
            return True
        except Exception as e:
            logger.error(f"[{api_symbol}] Błąd podczas ustawiania Trailing Stop: {e}", exc_info=True)
            return False