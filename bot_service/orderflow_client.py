# bot_service/orderflow_client.py
import requests
import time
from typing import Optional, Dict, Any
from logging import getLogger
from requests.exceptions import RequestException, Timeout, JSONDecodeError

logger = getLogger(__name__)

class OrderFlowClient:
    """
    Klient do komunikacji z OrderFlow Engine.
    
    ZMIANY W WERSJI 2.0:
    - Dodano retry logic (2 próby z exponential backoff)
    - Walidacja Content-Type przed parsowaniem JSON
    - Rozróżnienie Timeout vs ConnectionError
    - Timeout 3s zamiast nieograniczonego
    """
    
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'TradingBot/2.0'})
        logger.info(f"OrderFlowClient zainicjalizowany: {base_url}")
    
    def get_metrics(self, symbol: str, max_retries: int = 2) -> Optional[Dict[str, Any]]:
        """
        Pobiera metryki OrderFlow dla symbolu.
        
        Args:
            symbol: Symbol bez .P (np. "ADAUSDT")
            max_retries: Liczba prób (domyślnie 2)
        
        Returns:
            Dict z metrykami lub None w przypadku błędu
        """
        endpoint = f"{self.base_url}/metrics"
        
        for attempt in range(max_retries):
            try:
                response = self.session.get(
                    endpoint,
                    params={"symbol": symbol},
                    timeout=3  # 3 sekundy timeout
                )
                
                # Sprawdź status code
                response.raise_for_status()
                
                # Walidacja Content-Type
                content_type = response.headers.get('Content-Type', '')
                if 'application/json' not in content_type:
                    logger.error(
                        f"OrderFlow zwrócił nie-JSON dla {symbol}. "
                        f"Content-Type: {content_type}. "
                        f"Body preview: {response.text[:200]}"
                    )
                    return None
                
                # Parsowanie JSON
                data = response.json()
                
                # Walidacja struktury odpowiedzi
                if not isinstance(data, dict):
                    logger.error(f"OrderFlow zwrócił nieprawidłową strukturę dla {symbol}: {type(data)}")
                    return None
                
                # Success
                logger.debug(f"OrderFlow metrics for {symbol}: m2_delta={data.get('m2_delta')}, m5_rs_ratio={data.get('m5_rs_ratio')}")
                return data
            
            except Timeout:
                logger.warning(
                    f"OrderFlow timeout (próba {attempt + 1}/{max_retries}) dla {symbol}. "
                    f"Endpoint: {endpoint}"
                )
                if attempt < max_retries - 1:
                    time.sleep(0.5 * (2 ** attempt))  # Exponential backoff: 0.5s, 1s
                else:
                    logger.error(f"OrderFlow timeout po {max_retries} próbach dla {symbol}")
                    return None
            
            except JSONDecodeError as e:
                logger.error(
                    f"OrderFlow zwrócił nieprawidłowy JSON dla {symbol}. "
                    f"Błąd: {e}. Body preview: {response.text[:200]}"
                )
                return None
            
            except RequestException as e:
                logger.warning(
                    f"OrderFlow error (próba {attempt + 1}/{max_retries}) dla {symbol}: "
                    f"{type(e).__name__} - {e}"
                )
                if attempt < max_retries - 1:
                    time.sleep(0.5 * (2 ** attempt))
                else:
                    logger.error(f"OrderFlow error po {max_retries} próbach dla {symbol}")
                    return None
            
            except Exception as e:
                logger.error(
                    f"Nieoczekiwany błąd w OrderFlowClient dla {symbol}: {e}",
                    exc_info=True
                )
                return None
        
        return None
    
    def health_check(self) -> bool:
        """
        Sprawdza czy OrderFlow Engine jest dostępny.
        
        Returns:
            True jeśli /health zwraca 200, False w przeciwnym razie
        """
        try:
            response = self.session.get(
                f"{self.base_url}/health",
                timeout=2
            )
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"OrderFlow health check failed: {e}")
            return False