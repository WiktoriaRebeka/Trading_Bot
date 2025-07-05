# Lokalizacja: shared_lib/models.py
# WERSJA PRODUKCYJNA - OSTATECZNA I ZGODNA Z WEBHOOKIEM

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime

class AlertData(BaseModel):
    """
    Model reprezentujący surowe dane alertu przychodzącego z TradingView.
    Definicja pól jest w 100% zgodna z kluczami JSON z webhooka.
    """
    # Pola opcjonalne, dodawane po stronie serwera
    id: Optional[str] = None
    received_at: Optional[datetime] = None
    
    # Pola przychodzące z webhooka
    symbol: str
    direction_code: int = Field(alias='directionCode')
    direction: Optional[str] = None
    entry: float
    sl: float
    tp: float
    timestamp: str
    
    # --- OSTATECZNA POPRAWKA PÓL ---
    # Nazwy pól w modelu muszą DOKŁADNIE odpowiadać kluczom w JSON-ie z webhooka.
    # Nie używamy już aliasów, ponieważ nazwy pól są zgodne z konwencją.
    tp_1_0: float
    tp_1_5: float
    tp_2_0: float
    tp_3_0: float
    tp_4_0: float
    tp_5_0: float
    
    class Config:
        # allow_population_by_field_name jest domyślnie True, ale zostawmy dla jasności.
        allow_population_by_field_name = True
        # Ignoruje dodatkowe, nieistotne pola z webhooka (np. 'source', 'type')
        extra = 'ignore'

class SetupData(BaseModel):
    """Model reprezentujący aktywny setup tradingowy w Firestore."""
    alert_data: AlertData
    entry_attempts: int = 0
    is_position_open_on_this_setup: bool = False
    is_reset_needed_after_loss: bool = False
    updated_at: datetime

class OpenTradeData(BaseModel):
    """Model reprezentujący otwartą pozycję w Firestore."""
    trade_id: str
    symbol: str
    direction: str
    ob_type: str
    entry_price: float
    sl_price: float
    tp_price: float
    opened_at_ms: int
    opened_at_iso: str
    alert_data_snapshot: Dict[str, Any]

class AnalyzedTradeData(BaseModel):
    """Model reprezentujący "ducha" pozycji do analizy post-mortem."""
    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    original_sl: float
    opened_at_ms: int
    alert_data_snapshot: Dict[str, Any]
    last_analysis_timestamp_ms: int
    last_known_extreme_price: float
    last_bq_update_iso: Optional[datetime] = None