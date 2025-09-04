# Lokalizacja: shared_lib/config.py (NOWY PLIK)

import os

class AppConfig:
    """
    Klasa przechowująca dynamicznie ładowaną konfigurację, np. sekrety.
    Działa jako singleton.
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
        
        if self.BYBIT_API_KEY and self.BYBIT_API_SECRET:
            self.is_loaded = True
        else:
            # To pozwoli na łatwiejsze debugowanie w przyszłości
            print("OSTRZEŻENIE: Klucze API Bybit nie zostały załadowane do konfiguracji.")
            self.is_loaded = False

# Tworzymy jedną, globalną instancję konfiguracji
config = AppConfig()