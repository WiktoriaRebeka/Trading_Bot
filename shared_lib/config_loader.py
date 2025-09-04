# Lokalizacja: shared_lib/config_loader.py

import os
import logging
from dotenv import load_dotenv
from shared_lib.config import config

logger = logging.getLogger(__name__)

def load_config():
    """
    Ładuje konfigurację w zależności od środowiska.
    W Cloud Run zmienne środowiskowe są już wstrzyknięte przez konfigurację wdrożenia.
    Lokalnie, ładuje je z pliku .env.
    """
    if 'K_SERVICE' not in os.environ:
        logger.info("Środowisko lokalne. Próba ładowania konfiguracji z pliku .env.")
        _load_from_dotenv()
    else:
        logger.info("Wykryto środowisko Cloud Run. Zmienne środowiskowe powinny być już dostępne.")

    # Ta funkcja wczyta zmienne (z .env lub wstrzyknięte przez Cloud Run) do obiektu config
    config.load()
    logger.info("Obiekt konfiguracyjny został zaktualizowany.")

def _load_from_dotenv():
    """Ładuje konfigurację z lokalnego pliku .env."""
    # Używamy ścieżki względnej, aby uniknąć problemów z __file__
    dotenv_path = os.path.join(os.getcwd(), '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)
        logger.info(f"Pomyślnie załadowano zmienne z pliku: {dotenv_path}")
    else:
        # W drugim kroku szukamy w katalogu nadrzędnym
        dotenv_path_parent = os.path.join(os.path.dirname(os.getcwd()), '.env')
        if os.path.exists(dotenv_path_parent):
            load_dotenv(dotenv_path=dotenv_path_parent)
            logger.info(f"Pomyślnie załadowano zmienne z pliku: {dotenv_path_parent}")
        else:
            logger.warning(f"Plik .env nie został znaleziony w {os.getcwd()} ani w katalogu nadrzędnym.")