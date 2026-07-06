# Lokalizacja: bot_service/bybit_executor.py

import asyncio
import logging
import time
import hmac
import hashlib
import json
import math
from typing import Dict, Any, Optional, List, Tuple
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
  
    def place_order_sync(self, params: Dict[str, Any]) -> Optional[Dict[str, str]]:
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
        
        optional_params = ["price", "stopLoss", "takeProfit", "slTriggerBy", "orderLinkId", "timeInForce"]
        for param in optional_params:
            if param in params:
                val = params[param]
                if param in ("stopLoss", "takeProfit", "price") and val is not None:
                    text = format(float(val), "f")
                    if "." in text:
                        text = text.rstrip("0").rstrip(".")
                    payload[param] = text or "0"
                else:
                    payload[param] = str(val)

        if params.get("takeProfit") or params.get("stopLoss"):
            payload["tpslMode"] = "Full"

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

    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "Market",
        price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        time_in_force: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": qty,
        }
        if price is not None:
            params["price"] = price
        if take_profit is not None:
            params["takeProfit"] = take_profit
        if stop_loss is not None:
            params["stopLoss"] = stop_loss
        if time_in_force is not None:
            params["timeInForce"] = time_in_force
        if event_id:
            params["orderLinkId"] = event_id
        return await asyncio.to_thread(self.place_order_sync, params)

    def cancel_order_by_link_id(self, symbol: str, order_link_id: str) -> bool:
        """Anuluje pojedyncze zlecenie po orderLinkId (np. stary limit MSI)."""
        if not order_link_id:
            logger.error("[%s] cancel_order_by_link_id: brak order_link_id", symbol)
            return False
        api_symbol = symbol.replace(".P", "")
        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "orderLinkId": order_link_id,
        }
        logger.info("[%s] Anulowanie zlecenia orderLinkId=%s", symbol, order_link_id)
        try:
            self._send_request("POST", "/v5/order/cancel", params=payload)
            logger.info("[%s] Anulowano zlecenie orderLinkId=%s", symbol, order_link_id)
            return True
        except BybitAPIError as e:
            if e.ret_code in (110001, 110021, 110008):
                logger.info(
                    "[%s] Zlecenie orderLinkId=%s już nie istnieje (code=%s) — traktuję jako anulowane",
                    symbol, order_link_id, e.ret_code,
                )
                return True
            logger.warning(
                "[%s] Błąd API przy anulowaniu orderLinkId=%s: %s",
                symbol, order_link_id, e,
            )
            return False
        except Exception as e:
            logger.error(
                "[%s] Nieoczekiwany błąd anulowania orderLinkId=%s: %s",
                symbol, order_link_id, e, exc_info=True,
            )
            return False

    def set_trailing_stop_for_position(
        self,
        symbol: str,
        trailing_stop: str,
        active_price: Optional[str] = None,
    ) -> bool:
        api_symbol = symbol.replace('.P', '')
        payload: Dict[str, Any] = {
            "category": "linear",
            "symbol": api_symbol,
            "tpslMode": "Full",
            "trailingStop": trailing_stop,
            "positionIdx": 0,
        }
        if active_price is not None:
            payload["activePrice"] = active_price
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

    def get_closed_pnl_history(
        self, start_time_ms: int, limit: int = 100
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Zwraca (lista_rekordów, fetch_complete).
        fetch_complete=False gdy przerwano paginację przez błąd HTTP/API — caller NIE powinien
        przesuwać kursora czasowego.
        """
        endpoint = "/v5/position/closed-pnl"
        all_pnl_records: List[Dict[str, Any]] = []
        cursor = None
        fetch_complete = True
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
                fetch_complete = False
                break
        logger.info(f"Pobrano {len(all_pnl_records)} rekordów P&L (fetch_complete={fetch_complete}).")
        return list(reversed(all_pnl_records)), fetch_complete

    # <<< POPRAWKA #1 - BARDZIEJ NIEZAWODNA WERSJA >>>
    def get_open_position_side(self, symbol: str) -> Optional[str]:
        try:
            position_info = self.get_position_info(symbol) # Używa już poprawionej, niezawodnej logiki
            if position_info:
                side = position_info.get("side")
                if side == "Buy":
                    return "LONG"
                elif side == "Sell":
                    return "SHORT"
            return None
        except Exception as e:
            logger.error(
                f"[{symbol}] Błąd API podczas sprawdzania otwartych pozycji — "
                f"traktuję jak brak pozycji (nie blokuję alertu): {e}",
                exc_info=True,
            )
            return None

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

    def find_order_details_by_link_id(self, symbol: str, order_link_id: str) -> Optional[Dict[str, Any]]:
        """
        Niezawodnie wyszukuje szczegóły zlecenia po orderLinkId, sprawdzając najpierw
        aktywne zlecenia, a następnie historię. Zawsze używa symbolu do optymalizacji zapytania.
        """
        if not symbol or not order_link_id:
            logger.error("Próba wyszukania zlecenia bez symbolu lub order_link_id.")
            return None

        api_symbol = symbol.replace('.P', '')
        log_prefix = f"[{api_symbol}|{order_link_id}]"

        # 1. Sprawdź w czasie rzeczywistym (dla zleceń 'New', 'PartiallyFilled')
        try:
            endpoint_realtime = "/v5/order/realtime"
            params_realtime = {"category": "linear", "symbol": api_symbol, "orderLinkId": order_link_id}
            result_realtime = self._send_request("GET", endpoint_realtime, params=params_realtime)
            if result_realtime and result_realtime.get('list'):
                logger.debug(f"{log_prefix} Znaleziono zlecenie w czasie rzeczywistym (realtime).")
                return result_realtime['list'][0]
        except BybitAPIError as e:
            # Ignorujemy błąd "order does not exist", bo to oczekiwane, jeśli zlecenie jest już w historii
            if e.ret_code not in [110001, 110021]: 
                logger.warning(f"{log_prefix} Błąd API podczas sprawdzania zleceń w czasie rzeczywistym: {e}")
        except Exception as e:
            logger.error(f"{log_prefix} Nieoczekiwany błąd podczas sprawdzania zleceń w czasie rzeczywistym: {e}", exc_info=True)

        # 2. Jeśli nie znaleziono, sprawdź w historii (dla zleceń 'Filled', 'Cancelled', 'Rejected')
        try:
            endpoint_history = "/v5/order/history"
            # <<< KLUCZOWA ZMIANA: Zawsze dodajemy 'symbol' do parametrów zapytania o historię >>>
            params_history = {"category": "linear", "symbol": api_symbol, "orderLinkId": order_link_id}
            result_history = self._send_request("GET", endpoint_history, params=params_history)
            if result_history and result_history.get('list'):
                logger.debug(f"{log_prefix} Znaleziono zlecenie w historii.")
                return result_history['list'][0]
        except Exception as e:
            logger.error(f"{log_prefix} Błąd podczas sprawdzania historii zleceń: {e}", exc_info=True)

        # 3. Jeśli nigdzie nie znaleziono
        logger.warning(f"{log_prefix} Nie znaleziono zlecenia ani w czasie rzeczywistym, ani w historii.")
        return None


    def get_active_tp_sl_orders(self, symbol: str) -> List[Dict[str, Any]]:
        api_symbol = symbol.replace('.P', '')
        endpoint = "/v5/order/realtime"
        params = { "category": "linear", "symbol": api_symbol, "orderFilter": "StopOrder" }
        try:
            result = self._send_request("GET", endpoint, params=params)
            order_list = result.get('list', [])
            logger.debug(f"[{symbol}] Znaleziono {len(order_list)} aktywnych zleceň TP/SL.")
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
        
        params = {"category": "linear", "settleCoin": "USDT"}
        
        try:
            result = self._send_request("GET", "/v5/position/list", params=params)
            
            if result and result.get('list'):
                for position_data in result['list']:
                    if position_data.get('symbol') == api_symbol and float(position_data.get("size", "0")) > 0:
                        logger.debug(f"[{symbol}] Znaleziono aktywną pozycję dla {api_symbol} na liście pozycji USDT.")
                        return position_data
            
            logger.debug(f"[{symbol}] Nie znaleziono aktywnej pozycji dla symbolu {api_symbol} na liście wszystkich otwartych pozycji USDT.")
            return None
            
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Błąd podczas pobierania informacji o pozycji: {e}", exc_info=True)
            return None

    def find_sl_order_id(self, symbol: str, order_data: Dict[str, Any]) -> Optional[str]:
        try:
            active_stop_orders = self.get_active_tp_sl_orders(symbol)
            for stop_order in active_stop_orders:
                trigger_price = float(stop_order.get('triggerPrice', 0))
                if stop_order.get('stopOrderType') == 'StopLoss' and math.isclose(trigger_price, order_data.get('planned_sl_price')):
                    sl_order_id = stop_order.get('orderId')
                    logger.info(f"[{symbol}] Znaleziono pasujące zlecenie SL o ID: {sl_order_id}")
                    return sl_order_id
            logger.warning(f"[{symbol}] Nie znaleziono aktywnego zlecenia SL pasującego do ceny {order_data.get('planned_sl_price')}.")
            return None
        except Exception as e:
            logger.warning(f"[{symbol}] Nie udało się pobrać ID zlecenia SL: {e}")
            return None

    def get_klines_1m(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> List[Dict[str, Any]]:
        """
        Świece 1M z Bybit w [start_ms, end_ms] (ms epoch, UTC).
        Zwraca listę {ts, open, high, low, close} posortowaną rosnąco po ts.
        """
        if start_ms > end_ms:
            return []
        api_symbol = symbol.replace(".P", "")
        by_ts: Dict[int, Dict[str, Any]] = {}
        cursor_end = end_ms

        while True:
            params: Dict[str, Any] = {
                "category": "linear",
                "symbol": api_symbol,
                "interval": "1",
                "start": start_ms,
                "end": cursor_end,
                "limit": 200,
            }
            result = self._send_request("GET", "/v5/market/kline", params=params)
            raw_list = result.get("list") or []
            if not raw_list:
                break

            for k in raw_list:
                ts = int(k[0])
                if ts < start_ms or ts > end_ms:
                    continue
                by_ts[ts] = {
                    "ts": ts,
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                }

            if len(raw_list) < 200:
                break
            oldest_ts = int(raw_list[-1][0])
            if oldest_ts <= start_ms:
                break
            cursor_end = oldest_ts - 1

        return [by_ts[k] for k in sorted(by_ts)]

    def get_latest_prices(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        if not symbols:
            return {}
        
        endpoint = "/v5/market/tickers"
        params = {"category": "linear"}
        
        if len(symbols) == 1:
            params["symbol"] = symbols[0].replace('.P', '')
        
        try:
            result = self._send_request("GET", endpoint, params=params)
            price_data = {}
            if result and result.get('list'):
                for ticker in result['list']:
                    full_symbol = f"{ticker.get('symbol')}.P"
                    price_data[full_symbol] = ticker
                logger.debug(f"Pobrano aktualne ceny dla {len(price_data)}/{len(symbols)} symboli.")
                return price_data
            return {}
        except Exception as e:
            logger.error(f"Błąd podczas pobierania aktualnych cen: {e}")
            return {}