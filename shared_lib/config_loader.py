# Lokalizacja: shared_lib/config_loader.py

import os
import logging
from google.cloud import secretmanager
from io import StringIO
from dotenv import load_dotenv
import requests


from shared_lib.config import config
from shared_lib import constants

logger = logging.getLogger(__name__)

def _get_project_id_from_metadata():
    """Pobiera ID projektu z serwera metadanych GCP."""
    try:
        metadata_url = "http://metadata.google.internal/computeMetadata/v1/project/project-id"
        headers = {"Metadata-Flavor": "Google"}
        response = requests.get(metadata_url, headers=headers, timeout=2)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.warning(f"Nie udało się pobrać ID projektu z serwera metadanych: {e}")
        return None

def load_config():
    """
    Ładuje konfigurację w zależności od środowiska i aktualizuje globalny obiekt konfiguracyjny.
    """
    # --- DODATKOWY LOG ---
    if 'K_SERVICE' in os.environ:
        logger.info(f"Wykryto środowisko Cloud Run (K_SERVICE={os.environ['K_SERVICE']}). Ładowanie konfiguracji z Secret Manager.")
        _load_from_secret_manager()
    else:
        logger.warning("Nie wykryto zmiennej K_SERVICE. Zakładam środowisko lokalne. Ładowanie konfiguracji z pliku .env.")
        _load_from_dotenv()
    # --- KONIEC DODATKOWEGO LOGU ---
  
    config.load()
    logger.info("Obiekt konfiguracyjny został zaktualizowany.")

def _load_from_secret_manager():
    """
    Pobiera dedykowane sekrety ('bybit-api-key', 'bybit-api-secret') z GCP Secret Manager
    i ustawia je jako zmienne środowiskowe.
    """
    project_id = _get_project_id_from_metadata() or constants.GCP_PROJECT_ID
    
    # Lista sekretów, których ta aplikacja potrzebuje do działania.
    secrets_to_load = [
        "bybit-api-key",
        "bybit-api-secret"
    ]

    try:
        client = secretmanager.SecretManagerServiceClient()
        
        for secret_id in secrets_to_load:
            # Używamy 'latest', ponieważ dla tych sekretów nie ma problemu ze zniszczoną wersją.
            name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
            logger.info(f"Próba dostępu do sekretu: {name}")
            
            response = client.access_secret_version(request={"name": name})
            secret_value = response.payload.data.decode("UTF-8").strip()
            
            # Konwertuj nazwę sekretu (np. 'bybit-api-key') na nazwę zmiennej środowiskowej (np. 'BYBIT_API_KEY')
            env_var_name = secret_id.upper().replace("-", "_")
            
            # Ustaw zmienną środowiskową
            os.environ[env_var_name] = secret_value
            
            logger.info(f"Pomyślnie załadowano i ustawiono zmienną środowiskową dla '{env_var_name}'.")

    except Exception as e:
        logger.critical(f"KRYTYCZNY BŁĄD podczas dostępu do Secret Manager: {e}", exc_info=True)
        raise RuntimeError("Nie udało się załadować konfiguracji z Secret Manager. Aplikacja nie może wystartować.")

def _load_from_dotenv():
    """Ładuje konfigurację z lokalnego pliku .env."""
    # --- POPRAWKA PONIŻEJ ---
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # --- KONIEC POPRAWKI ---
    dotenv_path = os.path.join(project_root, '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)
        # --- DODATKOWY LOG ---
        logger.info(f"Pomyślnie załadowano zmienne z pliku .env: {dotenv_path}")
    else:
        logger.warning(f"Plik .env nie został znaleziony w ścieżce: {dotenv_path}.")