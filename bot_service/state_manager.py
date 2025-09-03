import logging
from typing import Optional, Iterable, Dict, Any, List
from datetime import datetime, timezone
from google.cloud import firestore
from google.cloud.firestore_v1.document import DocumentSnapshot

from shared_lib.firebase_client import get_db
from shared_lib import constants
from shared_lib.models import AlertData, AnalyticalScenario

logger = logging.getLogger(__name__)

def _get_db() -> firestore.Client:
    """Zwraca instancję klienta Firestore."""
    return get_db()


def create_analytical_scenario(alert_data: AlertData):
    """Tworzy nowy dokument w 'analytical_scenarios' na podstawie alertu."""
    db = _get_db()
    doc_ref = db.collection('analytical_scenarios').document(alert_data.id)
    
    initial_status = {
        '1.0': 'ACTIVE', '1.5': 'ACTIVE', '2.0': 'ACTIVE',
        '3.0': 'ACTIVE', '4.0': 'ACTIVE', '5.0': 'ACTIVE'
    }
    
    scenario = AnalyticalScenario(
        alert_id=alert_data.id,
        symbol=alert_data.symbol,
        direction=alert_data.direction,
        entry_price=alert_data.entry,
        sl_price=alert_data.sl,
        tp_1_0=alert_data.tp_1_0,
        tp_1_5=alert_data.tp_1_5,
        tp_2_0=alert_data.tp_2_0,
        tp_3_0=alert_data.tp_3_0,
        tp_4_0=alert_data.tp_4_0,
        tp_5_0=alert_data.tp_5_0,
        scenario_status=initial_status,
        entry_status='PENDING',  
        created_at=datetime.now(timezone.utc)
    )
    
    doc_ref.set(scenario.model_dump())
    logger.info(f"[{alert_data.symbol}] Utworzono nową 'teczkę analityczną' dla alertu {alert_data.id}.")

def get_all_active_scenarios() -> Iterable[DocumentSnapshot]:
    """Pobiera wszystkie aktywne scenariusze analityczne."""
    return _get_db().collection('analytical_scenarios').stream()

def find_scenario_by_symbol(symbol: str) -> Optional[AnalyticalScenario]:
    """Wyszukuje aktywny scenariusz dla danego symbolu."""
    try:
        scenarios_ref = _get_db().collection('analytical_scenarios').where('symbol', '==', symbol).limit(1).stream()
        for scenario_doc in scenarios_ref:
            return AnalyticalScenario.model_validate(scenario_doc.to_dict())
        return None
    except Exception as e:
        logger.error(f"Błąd podczas wyszukiwania scenariusza dla symbolu {symbol}: {e}")
        return None

def update_analytical_scenario_status(alert_id: str, new_status: Dict[str, str]):
    """Aktualizuje statusy scenariuszy w dokumencie."""
    doc_ref = _get_db().collection('analytical_scenarios').document(alert_id)
    doc_ref.update({"scenario_status": new_status})
    logger.info(f"[Alert: {alert_id}] Zaktualizowano statusy scenariuszy w Firestore.")

def delete_analytical_scenario(alert_id: str):
    """Usuwa zakończony dokument analityczny."""
    _get_db().collection('analytical_scenarios').document(alert_id).delete()
    logger.info(f"[Alert: {alert_id}] Usunięto zakończoną 'teczkę analityczną' z Firestore.")

# --- FUNKCJE DOSTĘPU DO DANYCH RYNKOWYCH ---

def get_latest_klines_from_cache(symbols: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Pobiera najnowsze dane kline z cache'u w Firestore dla podanej listy symboli."""
    if not symbols: 
        return {}
    db = _get_db()
    klines_cache = {}
    unique_symbols = list(set(s for s in symbols if isinstance(s, str) and s))
    if not unique_symbols:
        return {}
        
    # Firestore ma limit 30 argumentów dla operatora 'in'
    for i in range(0, len(unique_symbols), 30):
        chunk = unique_symbols[i:i + 30]
        if not chunk: continue
        try:
            docs = db.collection(constants.LATEST_KLINES_COLLECTION).where("__name__", "in", chunk).stream()
            for doc in docs:
                klines_cache[doc.id] = doc.to_dict()
        except Exception as e:
            logger.error(f"[KLINE_CACHE] Błąd podczas pobierania danych dla części {chunk}: {e}", exc_info=True)
            continue
            
    if not klines_cache:
        logger.warning(f"[KLINE_CACHE] Nie udało się pobrać ŻADNYCH rekordów kline z cache'u dla symboli: {unique_symbols}.")
        
    return klines_cache

def update_analytical_scenario_entry_status(alert_id: str, new_status: str):
    """Aktualizuje status wejścia w dokumencie."""
    doc_ref = _get_db().collection('analytical_scenarios').document(alert_id)
    doc_ref.update({"entry_status": new_status})
    logger.info(f"[Alert: {alert_id}] Status wejścia zaktualizowany na {new_status}.")

def find_all_scenarios_by_symbol(symbol: str) -> List[AnalyticalScenario]:
    """Wyszukuje WSZYSTKIE aktywne scenariusze dla danego symbolu."""
    scenarios = []
    try:
        scenarios_ref = _get_db().collection('analytical_scenarios').where('symbol', '==', symbol).stream()
        for scenario_doc in scenarios_ref:
            scenarios.append(AnalyticalScenario.model_validate(scenario_doc.to_dict()))
        return scenarios
    except Exception as e:
        logger.error(f"Błąd podczas wyszukiwania scenariuszy dla symbolu {symbol}: {e}")
        return []