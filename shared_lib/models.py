# Lokalizacja: shared_lib/models.py
# WERSJA PRODUKCYJNA - OSTATECZNA I ZGODNA Z DANYMI W BAZIE

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime

class AlertData(BaseModel):
    """
    Model reprezentujący surowe dane alertu przychodzącego z TradingView.
    Definicja pól jest w 100% zgodna z kluczami w dokumentach Firestore.
    """
    id: Optional[str] = None
    received_at: Optional[datetime] = None
    
    symbol: str
    direction_code: int = Field(alias='directionCode')
    direction: Optional[str] = None
    entry: float
    sl: float
    tp: float
    timestamp: str
    
    # --- OSTATECZNA I POPRAWNA DEFINICJA PÓL TP ---
    # Nazwy pól w modelu DOKŁADNIE odpowiadają kluczom w Firestore.
    tp_1_0: float
    tp_1_5: float
    tp_2_0: float
    tp_3_0: float
    tp_4_0: float
    tp_5_0: float
    
    class Config:
        allow_population_by_field_name = True
        extra = 'ignore' # Ignoruje dodatkowe pola z webhooka (np. 'source', 'type')

class SetupData(BaseModel):
    alert_data: AlertData
    entry_attempts: int = 0
    is_position_open_on_this_setup: bool = False
    is_reset_needed_after_loss: bool = False
    updated_at: datetime

class OpenTradeData(BaseModel):
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
    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    original_sl: float
    original_tp_5_0: Optional[float] = None
    opened_at_ms: int
    alert_data_snapshot: Dict[str, Any]
    last_analysis_timestamp_ms: int
    last_known_extreme_price: float
    last_bq_update_iso: Optional[datetime] = None
