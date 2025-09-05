# Lokalizacja: shared_lib/config_loader.py

import logging
from shared_lib.config import config

logger = logging.getLogger(__name__)

def load_config():
    """
    Po prostu wywołuje metodę load() na globalnym obiekcie konfiguracyjnym.
    Cała logika ładowania jest teraz w klasie AppConfig.
    """
    logger.info("Rozpoczynam ładowanie konfiguracji aplikacji...")
    config.load()
    logger.info("Konfiguracja aplikacji załadowana.")