# Lokalizacja: shared_lib/config.py 

import os
import logging

logger = logging.getLogger(__name__)

class AppConfig:
    def __init__(self):
        self.BYBIT_API_KEY: str | None = None
        self.BYBIT_API_SECRET: str | None = None
        self.is_loaded = False

    def load(self):
        """
        Ładuje konfigurację bezpośrednio ze zmiennych środowiskowych.
        """
        self.BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
        self.BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")


        if self.BYBIT_API_KEY:
            logger.info(f"Odczytano BYBIT_API_KEY. Długość: {len(self.BYBIT_API_KEY)}.")
        else:
            logger.error("KRYTYCZNY BŁĄD: Zmienna środowiskowa BYBIT_API_KEY jest pusta lub nie istnieje!")

        if self.BYBIT_API_SECRET:
            logger.info("Odczytano BYBIT_API_SECRET.")
        else:
            logger.error("KRYTYCZNY BŁĄD: Zmienna środowiskowa BYBIT_API_SECRET jest pusta lub nie istnieje!")
        
        self.is_loaded = True

config = AppConfig()