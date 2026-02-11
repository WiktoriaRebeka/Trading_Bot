# Lokalizacja: shared_lib/secret_manager.py

import logging
from typing import Optional
from google.cloud import secretmanager

logger = logging.getLogger(__name__)

def get_secret(secret_id: str, project_id: str) -> Optional[str]:
    """
    Pobiera wartość sekretu z Google Secret Manager.
    
    Args:
        secret_id: Nazwa (ID) sekretu.
        project_id: ID projektu Google Cloud.

    Returns:
        Wartość sekretu jako string lub None w przypadku błędu.
    """
    try:
        client = secretmanager.SecretManagerServiceClient()
        # Budujemy pełną ścieżkę do najnowszej wersji sekretu
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        logger.info(f"Pobieranie sekretu: {secret_id}...")
        response = client.access_secret_version(request={"name": name})
        
        payload = response.payload.data.decode("UTF-8")
        logger.info(f"Pomyślnie pobrano sekret: {secret_id}.")
        return payload
    except Exception as e:
        logger.critical(f"KRYTYCZNY BŁĄD: Nie udało się pobrać sekretu '{secret_id}'. Błąd: {e}", exc_info=True)
        return None