import logging
import time
import hmac
import hashlib
import json
from typing import Optional, Dict, Any, Tuple
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
    Hermetyzuje logikę autoryzacji, składania zleceń i obsługi błędów.
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
        recv_window = "10000"  # Zwiększony recv_window dla większej tolerancji na opóźnienia sieciowe
        param_str = timestamp + self.api_key + recv_window + payload
        hash_val = hmac.new(bytes(self.api_secret, "utf-8"), param_str.encode("utf-8"), hashlib.sha256)
        return hash_val.hexdigest()

    def _send_request(self, method: str, endpoint: str, payload: Dict = None) -> Dict[str, Any]:
        """Wysyła podpisane zapytanie do API Bybit i obsługuje podstawowe błędy."""
        url = self.base_url + endpoint
        payload_str = json.dumps(payload) if payload else ""
        timestamp = str(int(time.time() * 1000))
        
        headers = {
            'X-B-API-KEY': self.api_key,
            'X-B-API-TIMESTAMP': timestamp,
            'X-B-API-SIGN': self._generate_signature(timestamp, payload_str),
            'X-B-API-RECV-WINDOW': '10000',
            'Content-Type': 'application/json'
        }
        
        try:
            response = self.session.request(method, url, headers=headers, data=payload_str, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("retCode") != 0:
                raise BybitAPIError(ret_code=data.get("retCode"), ret_msg=data.get("retMsg"))
            
            return data.get("result", {})
        except RequestException as e:
            logger.error(f"Błąd sieciowy podczas komunikacji z Bybit: {e}", exc_info=True)
            raise  # Rzucamy dalej, aby logika wyższego rzędu mogła zareagować
        except BybitAPIError as e:
            logger.error(f"Błąd API Bybit: {e}", extra={"json_fields": {"ret_code": e.ret_code, "ret_msg": e.ret_msg}})
            raise


    def get_instrument_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Pobiera informacje o instrumencie, w tym max_leverage i qty_step."""
        logger.info(f"[{symbol}] Pobieranie informacji o instrumencie z Bybit.")
        
        # --- NOWA LINIA: Usuwamy przyrostek .P z symbolu ---
        api_symbol = symbol.replace('.P', '')
        
        try:
            result = self._send_request(
                "GET",
                # --- ZMIANA: Używamy oczyszczonego symbolu ---
                f"/v5/market/instruments-info?category=linear&symbol={api_symbol}"
            )
            # API zwraca listę, nawet dla jednego symbolu.
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
        
        # --- NOWA LINIA: Usuwamy przyrostek .P z symbolu ---
        api_symbol = symbol.replace('.P', '')
        
        payload = {
            "category": "linear",
            # --- ZMIANA: Używamy oczyszczonego symbolu ---
            "symbol": api_symbol,
            "side": "Buy" if str(order_params['side']).upper() == 'LONG' else "Sell",
            "orderType": "Limit",
            "qty": str(order_params['qty']),
            "price": str(order_params['price']),
            "leverage": str(order_params['leverage']),
            "takeProfit": str(order_params['takeProfit']),
            "stopLoss": str(order_params['stopLoss']),
            "timeInForce": "GTC"  # Good-Til-Canceled
        }
        
        try:
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

def format_quantity(quantity: float, qty_step: str) -> str:
    """Formatuje wielkość zlecenia zgodnie z wymaganą precyzją (qty_step)."""
    qty_decimal = Decimal(str(quantity))
    step_decimal = Decimal(qty_step)
    # Używamy kwantyzacji z zaokrągleniem w dół, aby nie przekroczyć limitów
    formatted_qty = qty_decimal.quantize(step_decimal, rounding=ROUND_DOWN)
    return str(formatted_qty)