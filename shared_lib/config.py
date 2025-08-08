# Lokalizacja: shared_lib/config.py 
import os
import logging

logger = logging.getLogger(__name__)

class AppConfig:
    """
    Klasa przechowująca dynamicznie ładowaną konfigurację, np. sekrety.
    """
    def __init__(self):
        
        self.BYBIT_API_KEY: str | None = None
        self.BYBIT_API_SECRET: str | None = None
        self.is_loaded = False

    def load(self):
        """
        Ładuje konfigurację ze zmiennych środowiskowych, czyszcząc wartości z potencjalnych
        błędów formatowania (białe znaki, cudzysłowy).
        """
        raw_api_key = os.getenv("BYBIT_API_KEY")
        raw_api_secret = os.getenv("BYBIT_API_SECRET")

        if raw_api_key:
            self.BYBIT_API_KEY = raw_api_key.strip().strip('"\'')
           
            logger.info(f"Załadowano BYBIT_API_KEY (długość: {len(self.BYBIT_API_KEY)}, końcówka: '...{self.BYBIT_API_KEY[-4:]}')")
        else:
            logger.warning("Zmienna środowiskowa BYBIT_API_KEY nie została znaleziona.")

        if raw_api_secret:
            self.BYBIT_API_SECRET = raw_api_secret.strip().strip('"\'')
            
            logger.info(f"Załadowano BYBIT_API_SECRET (długość: {len(self.BYBIT_API_SECRET)})")
        else:
            logger.warning("Zmienna środowiskowa BYBIT_API_SECRET nie została znaleziona.")
        
        self.is_loaded = True

config = AppConfig()