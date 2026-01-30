# bot_service/orderflow_client.py
import requests
from typing import Optional, Dict, Any
from logging import getLogger

logger = getLogger(__name__)

class OrderFlowClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'TradingBot/1.0'})

    def get_metrics(self, symbol: str) -> Optional[Dict[str, Any]]:
        # Symbol wejściowy jest bez .P (np. ADAUSDT)
        endpoint = f"{self.base_url}/metrics"
        try:
            # Krótki timeout, ponieważ jest to krytyczne dla opóźnienia sygnału PUSH
            response = self.session.get(endpoint, params={"symbol": symbol}, timeout=3)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"OrderFlow Client: Nieudane pobranie metryk dla {symbol}. Serwis niedostępny lub wolny. {e}")
            return None