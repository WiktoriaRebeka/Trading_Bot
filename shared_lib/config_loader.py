# Lokalizacja: shared_lib/config_loader.py

import os
import logging
from dotenv import load_dotenv
from shared_lib.config import config

logger = logging.getLogger(__name__)

def load_config():
    """
    Ładuje konfigurację. W Cloud Run zmienne są już wstrzyknięte przez platformę.
    Lokalnie, ładuje je z pliku .env.
    """
    if 'K_SERVICE' not in os.environ:
        logger.info("Środowisko lokalne. Ładowanie konfiguracji z pliku .env.")
        _load_from_dotenv()
    else:
        logger.info("Środowisko Cloud Run. Zmienne środowiskowe powinny być już dostępne.")
  
    config.load()

def _load_from_dotenv():
    """Ładuje konfigurację z lokalnego pliku .env."""
    try:
        # Znajduje plik .env w głównym katalogu projektu
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dotenv_path = os.path.join(project_root, '.env')
        
        if os.path.exists(dotenv_path):
            load_dotenv(dotenv_path=dotenv_path)
            logger.info(f"Pomyślnie załadowano zmienne z pliku .env: {dotenv_path}")
        else:
            logger.warning(f"Plik .env nie został znaleziony w ścieżce: {dotenv_path}.")
    except Exception as e:
        logger.error(f"Błąd podczas ładowania pliku .env: {e}", exc_info=True)