# Lokalizacja: shared_lib/config.py (NOWY PLIK)

import os

class AppConfig:
    """
    Klasa przechowująca dynamicznie ładowaną konfigurację, np. sekrety.
    """
    def __init__(self):
        # Wartości domyślne ustawione na None
        self.BYBIT_API_KEY: str | None = None
        self.BYBIT_API_SECRET: str | None = None
        self.is_loaded = False
        
    def load(self):
        """
        Ładuje konfigurację ze zmiennych środowiskowych.
        Ta metoda powinna być wywołana PO załadowaniu sekretów do środowiska.
        """
        self.BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
        self.BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
        self.is_loaded = True

config = AppConfig()