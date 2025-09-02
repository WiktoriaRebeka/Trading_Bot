import logging
from typing import Dict, Any, Optional

from google.cloud import bigquery

logger = logging.getLogger(__name__)

# Zmienne globalne dla klienta i referencji do tabeli
bigquery_client: Optional[bigquery.Client] = None
REALIZED_TRADES_TABLE_REF = "trading-bot-463318.trading_analytics.realized_trades_pnl"

def initialize_pnl_logger() -> bool:
    """
    Inicjalizuje klienta BigQuery specjalnie dla PNL Loggera.
    Zwraca True w przypadku sukcesu.
    """
    global bigquery_client
    if bigquery_client is not None:
        logger.info("[PNL_LOGGER_INIT] Klient BigQuery jest już zainicjalizowany.")
        return True
    try:
        logger.info("[PNL_LOGGER_INIT] Próba inicjalizacji klienta BigQuery...")
        client = bigquery.Client()
        # Sprawdzamy, czy tabela istnieje, aby wcześnie wykryć błędy konfiguracyjne
        client.get_table(REALIZED_TRADES_TABLE_REF)
        bigquery_client = client
        logger.info(f"[PNL_LOGGER_INIT] Klient BigQuery pomyślnie zainicjalizowany. Tabela: {REALIZED_TRADES_TABLE_REF}")
        return True
    except Exception as e:
        logger.critical(f"[PNL_LOGGER_INIT] KRYTYCZNY BŁĄD: Inicjalizacja PNL Loggera nie powiodła się: {e}", exc_info=True)
        bigquery_client = None
        return False

def get_pnl_bigquery_client() -> bigquery.Client:
    """Zwraca zainicjalizowanego klienta lub zgłasza wyjątek."""
    if bigquery_client is None:
        raise RuntimeError("Klient BigQuery dla PNL Loggera nie został pomyślnie zainicjalizowany.")
    return bigquery_client

def log_realized_trade(trade_pnl_data: Dict[str, Any]):
    """
    Zapisuje dane o zrealizowanej transakcji do dedykowanej tabeli P&L w BigQuery.
    """
    trade_id = trade_pnl_data.get('trade_id', 'N/A')
    logger.info(f"[PNL_LOGGER][{trade_id}] Rozpoczynam zapis zrealizowanego P&L do BigQuery.")
    
    try:
        client = get_pnl_bigquery_client()
    except RuntimeError as e:
        logger.error(f"[PNL_LOGGER][{trade_id}] Nie można zalogować transakcji: {e}")
        return

    required_keys = ['trade_id', 'symbol', 'realized_pnl_usdt', 'final_result']
    if not all(key in trade_pnl_data for key in required_keys):
        logger.error(f"[PNL_LOGGER][{trade_id}] Brak kluczowych danych do zapisu. Pomijam.")
        return

    try:
        rows_to_insert = [trade_pnl_data]
        errors = client.insert_rows_json(REALIZED_TRADES_TABLE_REF, rows_to_insert)
        if not errors:
            logger.info(f"[PNL_LOGGER][{trade_id}] SUKCES! Pomyślnie zapisano zrealizowany P&L.")
        else:
            logger.error(f"[PNL_LOGGER][{trade_id}] Błąd podczas wstawiania wierszy P&L: {errors}")
    except Exception as e:
        logger.error(f"[PNL_LOGGER][{trade_id}] Krytyczny błąd podczas zapisu P&L: {e}", exc_info=True)