import logging
from typing import Dict, Any, Optional
from google.cloud import bigquery
from bot_service.bigquery_logger import get_bigquery_client, _get_bq_type

logger = logging.getLogger(__name__)

# Zdefiniujmy nazwę naszej nowej tabeli w jednym miejscu
REALIZED_TRADES_TABLE_REF = "trading-bot-463318.trading_analytics.realized_trades_pnl"

def log_realized_trade(trade_pnl_data: Dict[str, Any]):
    """
    Zapisuje dane o zrealizowanej transakcji do dedykowanej tabeli P&L w BigQuery.
    """
    trade_id = trade_pnl_data.get('trade_id', 'N/A')
    logger.info(f"[PNL_LOGGER][{trade_id}] Rozpoczynam zapis zrealizowanego P&L do BigQuery.")
    
    try:
        client = get_bigquery_client()
    except RuntimeError as e:
        logger.error(f"[PNL_LOGGER][{trade_id}] Nie można zalogować transakcji: {e}")
        return

    # Prosta walidacja kluczowych pól
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