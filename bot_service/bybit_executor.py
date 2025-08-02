# Lokalizacja: bot_service/bybit_executor.py

import logging
import time
import hmac
import hashlib
import json
from typing import Optional, Dict, Any
from decimal import Decimal, ROUND_DOWN

import requests
from requests.exceptions import RequestException

from shared_lib.config import config
from shared_lib import constants

logger = logging.getLogger(__name__)

# Dedykowany wyjątek dla błędów API Bybit
class BybitAPIError(Exception):
    def __init__(self, ret_code: int, ret_msg: str):
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        super().__init__(f"Bybit API Error: [Code: {ret_code}] {ret_msg}")

class BybitExecutor:
    """
    Klasa odpowiedzialna za komunikację z API Bybit V5.
    Hermetyzuje logikę autoryzacji, składania zleceň i obsługi błędów.
    """
    def __init__(self):
        if not config.is_loaded:
            raise RuntimeError("Konfiguracja (config) nie została załadowana. Uruchom config_loader.load_config().")
        
        self.api_key: str = config.BYBIT_API_KEY
        self.api_secret: str = config.BYBIT_API_SECRET
        self.base_url: str = constants.BYBIT_API_URL_V5
        self.session = requests.Session()
        
        if not self.api_key or not self.api_secret:
            raise ValueError("Klucze API Bybit nie są ustawione w konfiguracji.")

    def _generate_signature(self, timestamp: str, payload: str) -> str:
        """Generuje sygnaturę HMAC-SHA256."""
        recv_window = "10000"
        param_str = timestamp + self.api_key + recv_window + payload
        hash_val = hmac.new(bytes(self.api_secret, "utf-8"), param_str.encode("utf-8"), hashlib.sha256)
        return hash_val.hexdigest()

    def _send_request(self, method: str, endpoint: str, payload: Dict = None, is_json: bool = True) -> Dict[str, Any]:
        """Wysyła podpisane zapytanie do API Bybit, obsługując różne typy contentu."""
        url = self.base_url + endpoint
        
        # Przygotuj payload i sygnaturę
        payload_str = ""
        if payload:
            if is_json:
                payload_str = json.dumps(payload)
            else:
                # Dla application/x-www-form-urlencoded, sygnatura jest z parametrów URL
                payload_str = '&'.join([f'{k}={v}' for k, v in sorted(payload.items())])

        timestamp = str(int(time.time() * 1000))
        
        headers = {
            'X-B-API-KEY': self.api_key,
            'X-B-API-TIMESTAMP': timestamp,
            'X-B-API-SIGN': self._generate_signature(timestamp, payload_str),
            'X-B-API-RECV-WINDOW': '10000',
        }
        
        # Ustaw odpowiedni Content-Type
        if is_json:
            headers['Content-Type'] = 'application/json'
        else:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'

        try:
            # Użyj json= lub data= w zależności od typu zapytania
            if is_json:
                response = self.session.request(method, url, headers=headers, json=payload, timeout=10)
            else:
                response = self.session.request(method, url, headers=headers, data=payload, timeout=10)
                
            response.raise_for_status()
            data = response.json()

            if data.get("retCode") != 0:
                raise BybitAPIError(ret_code=data.get("retCode"), ret_msg=data.get("retMsg"))
            
            return data.get("result", {})
        except RequestException as e:
            logger.error(f"Błąd sieciowy podczas komunikacji z Bybit: {e}", exc_info=True)
            raise
        except BybitAPIError as e:
            logger.error(f"Błąd API Bybit: {e}", extra={"json_fields": {"ret_code": e.ret_code, "ret_msg": e.ret_msg}})
            raise


    def get_instrument_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Pobiera informacje o instrumencie, w tym max_leverage i qty_step."""
        logger.info(f"[{symbol}] Pobieranie informacji o instrumencie z Bybit.")
        api_symbol = symbol.replace('.P', '')
        
        try:
            # Zapytania GET nie mają payloadu, więc is_json nie ma znaczenia
            result = self._send_request(
                "GET",
                f"/v5/market/instruments-info?category=linear&symbol={api_symbol}"
            )
            if result and result.get('list'):
                instrument_data = result['list'][0]
                leverage_filter = instrument_data.get('leverageFilter', {})
                lot_size_filter = instrument_data.get('lotSizeFilter', {})
                
                info = {
                    "max_leverage": int(float(leverage_filter.get('maxLeverage', '1'))),
                    "qty_step": lot_size_filter.get('qtyStep', '0.001')
                }
                logger.info(f"[{symbol}] Pobrane informacje dla {api_symbol}: max_leverage={info['max_leverage']}, qty_step={info['qty_step']}")
                return info
            logger.warning(f"[{symbol}] API Bybit zwróciło pustą listę instrumentów dla {api_symbol}.")
            return None
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] Nie udało się pobrać informacji o instrumencie dla {api_symbol}: {e}")
            return None

    def place_limit_order(self, order_params: Dict[str, Any]) -> Optional[str]:
        """Składa zlecenie typu Limit na giełdzie Bybit."""
        symbol = order_params.get('symbol')
        logger.info(f"[{symbol}] Przygotowywanie zlecenia: {order_params}")
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
            # Zlecenia są wysyłane jako JSON, więc is_json=True (domyślne)
            result = self._send_request("POST", "/v5/order/create", payload)
            order_id = result.get("orderId")
            if order_id:
                logger.info(f"[{symbol}] SUKCES! Zlecenie dla {api_symbol} pomyślnie złożone. Order ID: {order_id}")
                return order_id
            else:
                logger.error(f"[{symbol}] Zlecenie dla {api_symbol} złożone, ale API nie zwróciło orderId. Odpowiedź: {result}")
                return None
        except (RequestException, BybitAPIError) as e:
            logger.error(f"[{symbol}] KRYTYCZNY BŁĄD: Nie udało się złożyć zlecenia dla {api_symbol}: {e}")
            return None

    def set_isolated_margin(self, symbol: str, leverage: int) -> bool:
        """Ustawia tryb Isolated Margin i dźwignię dla danego symbolu."""
        logger.info(f"[{symbol}] Próba ustawienia trybu Isolated Margin z dźwignią {leverage}x.")
        
        api_symbol = symbol.replace('.P', '')
        leverage_str = str(leverage)
        
        payload = {
            "category": "linear",
            "symbol": api_symbol,
            "buyLeverage": leverage_str,
            "sellLeverage": leverage_str,
            "tradeMode": 1  # 0: Cross Margin, 1: Isolated Margin
        }
        
        try:
            # --- KLUCZOWA POPRAWKA: Przekazujemy is_json=False ---
            self._send_request("POST", "/v5/position/set-leverage", payload, is_json=False)
            logger.info(f"[{symbol}] SUKCES! Pomyślnie ustawiono tryb Isolated Margin i dźwignię.")
            return True
        except (RequestException, BybitAPIError) as e:
            if isinstance(e, BybitAPIError) and e.ret_code == 110043:
                logger.warning(f"[{symbol}] Dźwignia i tryb margin są już poprawnie ustawione. Kontynuuję.")
                return True
            
            logger.error(f"[{symbol}] KRYTYCZNY BŁĄD: Nie udało się ustawić trybu Isolated Margin i dźwigni: {e}")
            return False

# Ta funkcja jest funkcją pomocniczą i poprawnie znajduje się poza klasą.
def format_quantity(quantity: float, qty_step: str) -> str:
    """Formatuje wielkość zlecenia zgodnie z wymaganą precyzją (qty_step)."""
    qty_decimal = Decimal(str(quantity))
    step_decimal = Decimal(qty_step)
    formatted_qty = qty_decimal.quantize(step_decimal, rounding=ROUND_DOWN)
    return str(formatted_qty)