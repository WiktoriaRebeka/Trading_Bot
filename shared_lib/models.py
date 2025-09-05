# Lokalizacja: shared_lib/models.py

from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List
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
    tp_1_0: float
    tp_1_5: float
    tp_2_0: float
    tp_3_0: float
    tp_4_0: float
    tp_5_0: float
    
    class Config:
        allow_population_by_field_name = True
        extra = 'ignore'

    @validator('direction', pre=True, always=True)
    def set_direction_from_code(cls, v, values):
        if 'direction_code' in values:
            code = values['direction_code']
            if code == 1:
                return "LONG"
            if code == -1:
                return "SHORT"
        return "UNKNOWN"

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
    bybit_order_id: str 

class Kline(BaseModel):
    timestamp: int
    high: float
    low: float
    close: float

class AnalyzedTradeData(BaseModel):
    trade_id: str
    symbol: str
    direction: str
    ob_type: str
    entry_price: float
    original_sl: float
    original_tp_5_0: Optional[float] = None
    opened_at_ms: int
    alert_data_snapshot: Dict[str, Any]
    last_known_extreme_price: float
    last_analysis_timestamp_ms: int
    achieved_tps: List[str] = []

class OrderData(BaseModel):
    symbol: str
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    margin_value_usdc: float
    leverage: int

    @validator('direction')
    def direction_must_be_valid(cls, v):
        if v.upper() not in ['LONG', 'SHORT']:
            raise ValueError('Kierunek musi być "LONG" lub "SHORT"')
        return v.upper()