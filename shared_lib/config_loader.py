#Lokalizacja: shared_lib/config_loader.py


import os
import logging
from google.cloud import secretmanager
from io import StringIO
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

def load_config():
    """
    Ładuje konfigurację w zależności od środowiska.
    - W Google Cloud Run: pobiera sekrety z GCP Secret Manager.
    - Lokalnie: ładuje zmienne z pliku .env.
    """
    if 'K_SERVICE' in os.environ:
        logger.info("Wykryto środowisko Cloud Run. Ładowanie konfiguracji z Secret Manager.")
        _load_from_secret_manager()
    else:
        logger.info("Środowisko lokalne. Ładowanie konfiguracji z pliku .env.")
        _load_from_dotenv()

def _load_from_secret_manager():
    """Pobiera konfigurację z GCP Secret Manager i ładuje ją do zmiennych środowiskowych."""
    project_id = os.getenv("GCP_PROJECT")
    secret_id = "trading-bot-secrets"  

    if not project_id:
        logger.critical("Zmienna środowiskowa GCP_PROJECT nie jest ustawiona. Nie można załadować sekretów.")
        return

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
    dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)
    else:
        logger.warning(f"Plik .env nie został znaleziony w ścieżce: {dotenv_path}. Aplikacja może nie działać poprawnie.")