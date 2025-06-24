# /trading_bot/bigquery_logger.py (Wersja Finalna)

import logging
from typing import Dict, Any, Optional
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError

import constants

logger = logging.getLogger(__name__)

# Zaktualizowany schemat zgodny z nową logiką
EXPECTED_SCHEMA = {
    "trade_id": str,
    "timestamp_entry": str,
    "timestamp_close": str,
    "symbol": str,
    "direction": str,
    "main_result": str,
    "ob_type": str,
    "risk_amount_price_diff": float,
    "max_profit_price_diff": float,
    "rr_achieved": float,
    "rr_1_0_achieved": bool,
    "rr_1_5_achieved": bool,
    "rr_2_0_achieved": bool,
    "rr_3_0_achieved": bool,
    "rr_4_0_achieved": bool,
    "rr_5_0_achieved": bool,
}

try:
    bigquery_client = bigquery.Client()
    TABLE_REF = f"{constants.BIGQUERY_PROJECT_ID}.{constants.BIGQUERY_DATASET_ID}.{constants.BIGQUERY_TABLE_ID}"
    logger.info(f"Klient BigQuery zainicjalizowany pomyślnie. Tabela: {TABLE_REF}")
except Exception as e:
    bigquery_client = None
    TABLE_REF = None
    logger.critical(f"Nie udało się zainicjalizować klienta BigQuery: {e}", exc_info=True)


def _validate_and_sanitize_data(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Sprawdza, czy dane pasują do schematu, konwertuje typy i obsługuje brakujące wartości."""
    sanitized_data = {}
    has_errors = False

    for key, expected_type in EXPECTED_SCHEMA.items():
        value = data.get(key)

        if value is None:
            if expected_type == str: sanitized_data[key] = "N/A"
            elif expected_type == float: sanitized_data[key] = 0.0
            elif expected_type == bool: sanitized_data[key] = False
            logger.warning(f"[BQ_VALIDATOR] Brakujące pole '{key}'. Ustawiono domyślną wartość: {sanitized_data[key]}")
            continue

        try:
            if expected_type == str and not isinstance(value, str): sanitized_data[key] = str(value)
            elif expected_type == float and not isinstance(value, float): sanitized_data[key] = float(value)
            elif expected_type == bool and not isinstance(value, bool): sanitized_data[key] = bool(value)
            else: sanitized_data[key] = value
        except (ValueError, TypeError) as e:
            logger.error(f"[BQ_VALIDATOR] Błąd konwersji typu dla klucza '{key}'. Oczekiwano {expected_type}, otrzymano {type(value)} (wartość: {value}). Błąd: {e}")
            has_errors = True
            continue
            
    if has_errors:
        return None

    return sanitized_data


def log_trade_to_bigquery(trade_data: Dict):
    """Waliduje, czyści, a następnie wstawia pojedynczy wiersz do BigQuery."""
    if not bigquery_client or not TABLE_REF:
        logger.error("[BQ_LOGGER] Klient BigQuery nie jest dostępny. Pomijam logowanie transakcji.")
        return

    logger.info(f"[BQ_LOGGER] Otrzymano dane do zapisu: {trade_data}")
    sanitized_trade_data = _validate_and_sanitize_data(trade_data)

    if sanitized_trade_data is None:
        logger.error(f"[BQ_LOGGER] Błąd walidacji danych dla transakcji {trade_data.get('trade_id')}. Zapis do BigQuery przerwany.")
        return

    logger.info(f"[BQ_LOGGER] Dane po walidacji, gotowe do zapisu: {sanitized_trade_data}")
    
    try:
        errors = bigquery_client.insert_rows_json(TABLE_REF, [sanitized_trade_data])
        if not errors:
            logger.info(f"[BQ_LOGGER] Pomyślnie zapisano transakcję {sanitized_trade_data.get('trade_id')} do BigQuery.")
        else:
            logger.error(f"[BQ_LOGGER] Wystąpiły błędy API podczas wstawiania danych do BigQuery dla {sanitized_trade_data.get('trade_id')}: {errors}")
    except GoogleAPICallError as e:
        logger.error(f"[BQ_LOGGER] Błąd API podczas zapisu do BigQuery dla {sanitized_trade_data.get('trade_id')}: {e}", exc_info=True)