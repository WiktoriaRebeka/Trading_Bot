# Lokalizacja: shared_lib/config.py 

import os
import logging

logger = logging.getLogger(__name__)

class AppConfig:
    """
    Klasa przechowująca dynamicznie ładowaną konfigurację.
    """
    def __init__(self):
        self.BYBIT_API_KEY: str | None = None
        self.BYBIT_API_SECRET: str | None = None
        self.is_loaded = False

    def load(self):
        """
        Ładuje konfigurację ze zmiennych środowiskowych.
        """
        self.BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
        self.BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

        # Logowanie diagnostyczne
        if self.BYBIT_API_KEY:
            logger.info(f"Załadowano BYBIT_API_KEY (długość: {len(self.BYBIT_API_KEY)}).")
        else:
            logger.warning("Zmienna środowiskowa BYBIT_API_KEY nie została znaleziona lub jest pusta.")

        if self.BYBIT_API_SECRET:
            logger.info("Załadowano BYBIT_API_SECRET.")
        else:
            logger.warning("Zmienna środowiskowa BYBIT_API_SECRET nie została znaleziona lub jest pusta.")
        
        self.is_loaded = True

config = AppConfig()