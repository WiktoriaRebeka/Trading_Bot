#Lokalizacja: shared_lib/config_loader.py

import os
import logging
from google.cloud import secretmanager
from io import StringIO
from dotenv import load_dotenv
import requests # Dodajemy nowy import

logger = logging.getLogger(__name__)

def _get_project_id_from_metadata():
    """Pobiera ID projektu z serwera metadanych GCP."""
    try:
        # Ten URL jest dostępny tylko wewnątrz środowiska GCP
        metadata_url = "http://metadata.google.internal/computeMetadata/v1/project/project-id"
        headers = {"Metadata-Flavor": "Google"}
        response = requests.get(metadata_url, headers=headers, timeout=2)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.warning(f"Nie udało się pobrać ID projektu z serwera metadanych: {e}")
        return None

def load_config():
    """Ładuje konfigurację w zależności od środowiska."""
    if 'K_SERVICE' in os.environ:
        logger.info("Wykryto środowisko Cloud Run. Ładowanie konfiguracji z Secret Manager.")
        _load_from_secret_manager()
    else:
        logger.info("Środowisko lokalne. Ładowanie konfiguracji z pliku .env.")
        _load_from_dotenv()

def _load_from_secret_manager():
    """Pobiera konfigurację z GCP Secret Manager."""
    project_id = _get_project_id_from_metadata()
    secret_id = "trading-bot-secrets"

    if not project_id:
        logger.critical("Nie można automatycznie ustalić ID projektu. Nie można załadować sekretów.")
        # Ustawiamy domyślne ID jako ostateczność
        project_id = "trading-bot-463318"
        logger.warning(f"Używam domyślnego ID projektu: {project_id}")

    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        
        payload = response.payload.data.decode("UTF-8")
        fake_file = StringIO(payload)
        load_dotenv(stream=fake_file, override=True)
        
        logger.info(f"Pomyślnie załadowano konfigurację z sekretu: {secret_id}")
    except Exception as e:
        logger.error(f"Nie udało się załadować konfiguracji z Secret Manager. Błąd: {e}", exc_info=True)

def _load_from_dotenv():
    """Ładuje konfigurację z lokalnego pliku .env."""
    # Szukamy pliku .env w katalogu głównym projektu
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dotenv_path = os.path.join(project_root, '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)
    else:
        logger.warning(f"Plik .env nie został znaleziony w ścieżce: {dotenv_path}.")