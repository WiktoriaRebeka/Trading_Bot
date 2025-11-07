# Lokalizacja: bot_service/bybit_executor.py

import logging
import time
import hmac
import hashlib
import json
import math
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
        
        if params.get("orderType") == "Limit":
            if not params.get("stopLoss"):
                logger.critical(f"[{symbol}] KRYTYCZNA PRÓBA WYSŁANIA ZLECENIA LIMIT BEZ STOP LOSSA! Zlecenie zablokowane.")
                return None

        api_symbol = symbol.replace('.P', '')
        payload = {"category": "linear", "symbol": api_symbol, "side": params['side'], "orderType": params['orderType'], "qty": str(params['qty'])}
        
        optional_params = ["price", "stopLoss", "slTriggerBy", "orderLinkId", "timeInForce"]
        
        for param in optional_params:
            if param in params:
                payload[param] = str(params[param])
                
        logger.info(f"[{symbol}] Wysyłanie zlecenia do Bybit: {payload}")
        try:
            result = self._send_request("POST", "/v5/order/create", params=payload)
            logger.info(f"[{symbol}] Odpowiedź Bybit na place_order: {result}")
            if result.get("orderId"):
                return result
            return None
        except BybitAPIError as e:
            logger.error(f"[{symbol}] Błąd API Bybit podczas składania zlecenia: {e}")
            raise
        except Exception as e:
            logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD podczas składania zlecenia: {e}", exc_info=True)
            raise

    def set_trailing_stop_for_position(self, symbol: str, trailing_stop: str) -> bool:
        api_symbol = symbol.replace('.P', '')
        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "trailingStop": trailing_stop,
            "positionIdx": 0 
        }
        logger.info(f"[{symbol}] Wysyłanie finalnego zlecenia ustawiającego Trailing Stop: {payload}")
        try:
            self._send_request("POST", "/v5/position/trading-stop", params=payload)
            logger.info(f"[{symbol}] SUKCES! Pomyślnie wysłano zlecenie ustawienia Trailing Stop.")
            return True
        except BybitAPIError as e:
            logger.error(f"[{symbol}] Błąd API podczas ustawiania Trailing Stop: [Code: {e.ret_code}] {e.ret_msg}")
            return False
        except Exception as e:
            logger.critical(f"[{symbol}] KRYTYCZNY BŁĄD podczas ustawiania Trailing Stop: {e}", exc_info=True)
            return False

    def find_sl_order_id(self, symbol: str, order_data: Dict[str, Any]) -> Optional[str]:
        try:
            active_stop_orders = self.get_active_tp_sl_orders(symbol)
            for stop_order in active_stop_orders:
                trigger_price = float(stop_order.get('triggerPrice', 0))
                if stop_order.get('stopOrderType') == 'StopLoss' and math.isclose(trigger_price, order_data.get('planned_sl_price')):
                    sl_order_id = stop_order.get('orderId')
                    logger.info(f"[{symbol}] Znaleziono pasujące zlecenie SL o ID: {sl_order_id}")
                    return sl_order_id
            return None
        except Exception as e:
            logger.warning(f"[{symbol}] Nie udało się pobrać ID zlecenia SL: {e}")
            return None

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
        if not order_id and not order_link_id:
            return None
        endpoint = "/v5/order/history"
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
        api_symbol = symbol.replace('.P', '')
        endpoint = "/v5/order/realtime"
        params = { "category": "linear", "symbol": api_symbol, "orderFilter": "StopOrder" }
        try:
            result = self._send_request("GET", endpoint, params=params)
            order_list = result.get('list', [])
            logger.info(f"[{symbol}] Znaleziono {len(order_list)} aktywnych zleceň TP/SL.")
            return order_list
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Błąd podczas pobierania aktywnych zleceň TP/SL: {e}")
            return []

    def close_position_market(self, symbol: str, qty: float, side: str) -> bool:
        api_symbol = symbol.replace('.P', '')
        close_side = "Sell" if side == "Buy" else "Buy"
        payload = {
            "category": "linear", "symbol": api_symbol, "side": close_side,
            "orderType": "Market", "qty": str(qty), "reduceOnly": True
        }
        logger.warning(f"[{api_symbol}] Wysyłanie awaryjnego zlecenia MARKET zamykającego pozycję: {payload}")
        try:
            self._send_request("POST", "/v5/order/create", params=payload)
            logger.info(f"[{api_symbol}] Awaryjne zlecenie zamknięcia wysłane pomyślnie.")
            return True
        except Exception as e:
            logger.critical(f"[{api_symbol}] KRYTYCZNY BŁĄD podczas wysyłania awaryjnego zlecenia zamknięcia: {e}", exc_info=True)
            return False


    def get_position_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        api_symbol = symbol.replace('.P', '')
        
        # --- OSTATECZNA POPRAWKA ---
        # Zamiast wysyłać puste zapytanie, prosimy o wszystkie pozycje
        # rozliczane w USDT. To jest prawidłowe i zalecane użycie tego endpointu.
        params = {"category": "linear", "settleCoin": "USDT"}
        
        try:
            result = self._send_request("GET", "/v5/position/list", params=params)
            
            if result and result.get('list'):
                # Iterujemy po liście wszystkich pozycji rozliczanych w USDT.
                for position_data in result['list']:
                    # Sprawdzamy, czy symbol pozycji pasuje do tego, którego szukamy
                    # ORAZ czy rozmiar pozycji jest większy od zera.
                    if position_data.get('symbol') == api_symbol and float(position_data.get("size", "0")) > 0:
                        logger.info(f"[{symbol}] Znaleziono aktywną pozycję dla {api_symbol} na liście pozycji USDT.")
                        return position_data # Zwracamy pasującą pozycję
            
            # Jeśli pętla się zakończy i nic nie znajdziemy, zwracamy None.
            logger.warning(f"[{symbol}] Nie znaleziono aktywnej pozycji dla symbolu {api_symbol} na liście wszystkich otwartych pozycji USDT.")
            return None
            
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Błąd podczas pobierania informacji o pozycji: {e}", exc_info=True)
            return None



    def find_sl_order_id(self, symbol: str, order_data: Dict[str, Any]) -> Optional[str]:
        """Znajduje ID aktywnego zlecenia Stop Loss pasującego do planowanej ceny."""
        try:
            active_stop_orders = self.get_active_tp_sl_orders(symbol)
            for stop_order in active_stop_orders:
                trigger_price = float(stop_order.get('triggerPrice', 0))
                # Szukamy tylko zlecenia, które jest Stop Lossem
                if stop_order.get('stopOrderType') == 'StopLoss' and math.isclose(trigger_price, order_data.get('planned_sl_price')):
                    sl_order_id = stop_order.get('orderId')
                    logger.info(f"[{symbol}] Znaleziono pasujące zlecenie SL o ID: {sl_order_id}")
                    return sl_order_id
            logger.warning(f"[{symbol}] Nie znaleziono aktywnego zlecenia SL pasującego do ceny {order_data.get('planned_sl_price')}.")
            return None
        except Exception as e:
            logger.warning(f"[{symbol}] Nie udało się pobrać ID zlecenia SL: {e}")
            return None


    def get_latest_prices(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """Pobiera najnowsze informacje (ticker) dla listy symboli."""
        if not symbols:
            return {}
        
        endpoint = "/v5/market/tickers"
        params = {"category": "linear"}
        
        # Jeśli jest tylko jeden symbol, możemy go podać bezpośrednio
        if len(symbols) == 1:
            params["symbol"] = symbols[0].replace('.P', '')
        
        try:
            result = self._send_request("GET", endpoint, params=params)
            price_data = {}
            if result and result.get('list'):
                for ticker in result['list']:
                    # Kluczem w naszej mapie jest symbol z ".P"
                    full_symbol = f"{ticker.get('symbol')}.P"
                    price_data[full_symbol] = ticker
                logger.info(f"Pobrano aktualne ceny dla {len(price_data)}/{len(symbols)} symboli.")
                return price_data
            return {}
        except Exception as e:
            logger.error(f"Błąd podczas pobierania aktualnych cen: {e}")
            return {}