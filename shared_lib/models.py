# Lokalizacja: shared_lib/models.py
# WERSJA PRODUKCYJNA

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime

class AlertData(BaseModel):
    """
    Model reprezentujący surowe dane alertu przychodzącego z TradingView,
    zgodny z formatem zapisywanym w Firestore.
    """
    # Pola, które są dodawane po stronie serwera
    id: Optional[str] = None  # ID jest dodawane po odczycie, więc jest opcjonalne
    received_at: Optional[datetime] = None # To samo dotyczy `received_at`
    
    # Pola przychodzące z webhooka
    symbol: str
    direction_code: int = Field(alias='directionCode')
    direction: Optional[str] = None # To pole jest dodawane później
    entry: float
    sl: float
    tp: float
    timestamp: str # Czas z TradingView
    
    # --- KLUCZOWA POPRAWKA ALIASÓW ---
    # Aliasy muszą DOKŁADNIE odpowiadać kluczom w JSONie z webhooka.
    tp_1_0: float = Field(alias='tp_1_0')
    tp_1_5: float = Field(alias='tp_1_5')
    tp_2_0: float = Field(alias='tp_2_0')
    tp_3_0: float = Field(alias='tp_3_0')
    tp_4_0: float = Field(alias='tp_4_0')
    tp_5_0: float = Field(alias='tp_5_0')
    
    class Config:
        allow_population_by_field_name = True
        extra = 'ignore' # Ignoruje dodatkowe pola (np. 'source', 'type', 'levelHigh' itd.)

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