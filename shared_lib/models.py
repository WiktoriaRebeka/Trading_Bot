# Lokalizacja: shared_lib/models.py
# WERSJA PRODUKCYJNA - FINALNA

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime

class AlertData(BaseModel):
    id: Optional[str] = None
    received_at: Optional[datetime] = None
    symbol: str
    direction_code: int = Field(alias='directionCode')
    direction: Optional[str] = None
    entry: float
    sl: float
    tp: float
    timestamp: str
    
    # --- OSTATECZNA POPRAWKA ALIASÓW ---
    # Mapujemy klucze z webhooka (tp1, tp2...) na nasze wewnętrzne nazwy (tp_1_0, tp_1_5...).
    tp_1_0: float = Field(alias='tp1')
    tp_1_5: float = Field(alias='tp2')
    tp_2_0: float = Field(alias='tp3')
    tp_3_0: float = Field(alias='tp4')
    tp_5_0: float = Field(alias='tp5')
    
    class Config:
        allow_population_by_field_name = True
        extra = 'ignore'

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
