from datetime import datetime, timezone
from typing import Optional, Literal
import logging
from .firebase_client import get_db
# Importujemy SERVER_TIMESTAMP z nowej biblioteki
from google.cloud.firestore_v1.base_query import SERVER_TIMESTAMP

logger = logging.getLogger(__name__)
POSITIONS_COLLECTION_FIRESTORE = "trading_positions_ob_only"

PositionStatus = Literal["opened", "closed"]
OB_Type = Literal["New OB", "Old OB"]

def log_position_event(
    symbol: str, 
    direction: str, 
    status: PositionStatus, 
    ob_type: OB_Type,
    price: float, 
    result: Optional[Literal["WIN", "LOSE"]] = None
):
    """Loguje zdarzenie otwarcia lub zamknięcia pozycji do Firestore."""
    try:
        db = get_db()
    except Exception as e:
        logger.error(f"[POS_LOGGER] Błąd pobierania klienta DB: {e}", exc_info=True)
        return

    doc_id = f"{symbol}_{status}_{datetime.now(timezone.utc).isoformat()}"
    doc_ref = db.collection(POSITIONS_COLLECTION_FIRESTORE).document(doc_id)

    log_data = {
        "timestamp": SERVER_TIMESTAMP, # Używamy poprawnego importu
        "symbol": symbol,
        "direction": direction,
        "status": status,
        "ob_type": ob_type,
        "price": price,
        "result": result
    }

    try:
        doc_ref.set(log_data)
        logger.info(f"[POS_LOGGER] Zalogowano zdarzenie: {status} dla {symbol} ({ob_type})")
    except Exception as e:
        logger.error(f"[POS_LOGGER] Błąd zapisu do Firestore: {e}", exc_info=True)