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
    """Pobiera konfigurację z GCP Secret Manager."""
    project_id = _get_project_id_from_metadata() or constants.GCP_PROJECT_ID
    secret_id = "trading-bot-secrets"

    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        
        payload = response.payload.data.decode("UTF-8")

        # --- SONDA DIAGNOSTYCZNA ---
        # Logujemy surową zawartość pobraną z sekretu, aby zweryfikować, co dokładnie otrzymuje aplikacja.
        logger.info(f"DIAGNOSTYKA: Surowa zawartość pobrana z Secret Manager: \n---\n{payload}\n---")
        
        fake_file = StringIO(payload)
        load_dotenv(stream=fake_file, override=True)
        
        logger.info(f"Pomyślnie załadowano zmienne środowiskowe z sekretu: {secret_id}")
    except Exception as e:
        logger.error(f"Nie udało się załadować konfiguracji z Secret Manager. Błąd: {e}", exc_info=True)

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