#trading_bot/shared_lib/models.py


from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime

# Używamy aliasów, aby nazwy pól w Pythonie były zgodne z konwencją (snake_case),
# a jednocześnie mapowały się na oryginalne nazwy z JSON (camelCase).

class AlertData(BaseModel):
    """Model reprezentujący surowe dane alertu przychodzącego z TradingView."""
    id: str  # ID dokumentu z Firestore
    symbol: str
    direction_code: int = Field(alias='directionCode')
    direction: Optional[str] = None # To pole jest dodawane później w main.py
    entry: float
    sl: float
    tp: float
    tp_1_0: float = Field(alias='tp1')
    tp_1_5: float = Field(alias='tp2')
    tp_2_0: float = Field(alias='tp3')
    tp_3_0: float = Field(alias='tp4')
    tp_5_0: float = Field(alias='tp5')
    timestamp: str # Czas z TradingView
    received_at: datetime # Czas z serwera GCP
    
    class Config:
        allow_population_by_field_name = True # Umożliwia używanie obu nazw (aliasu i nazwy pola)
        extra = 'ignore' # Ignoruje dodatkowe pola, które mogą przyjść w JSON

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
    alert_data_snapshot: Dict[str, Any] # Zachowujemy jako dict, bo to zamrożony snapshot

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