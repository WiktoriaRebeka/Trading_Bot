# Lokalizacja: shared_lib/models.py (Pełna, zaktualizowana wersja)

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any

class AlertData(BaseModel):
    # Mapowanie pól z JSONa Sierry na model Pythona
    signal_id: str
    event_id: str # NOWE: Unikalny ID zdarzenia z C++
    symbol: str = Field(alias='id_symbol') # CF używa id_symbol, bot używa symbol
    timestamp: str
    direction: str
    entry: float
    sl: float
    tp: float
    risk_pct: float
    rr: float = Field(alias='rr')
    structure_state: int
    
    # Pola opcjonalne (NULLABLE w BigQuery)
    bos_high: Optional[bool] = False
    bos_low: Optional[bool] = False
    choch_up: Optional[bool] = False
    choch_down: Optional[bool] = False
    liquidity_grab_above: Optional[bool] = False
    liquidity_grab_below: Optional[bool] = False
    liquidity_price: Optional[float] = None
    eqh_detected: Optional[bool] = False
    eql_detected: Optional[bool] = False
    
    # --- DODANE POLA (Wymagane przez bot_logic.py i C++) ---
    risk_usdt: float = 0.0
    m2_delta: float = 0.0
    m5_rs_ratio: float = 0.0
    
    # ETAP 1: TIME FEATURES
    session: str = "UNKNOWN"
    minute_of_day: int = -1
    day_of_week: int = -1
    second: int = -1
    
    # ETAP 2: VOLATILITY FEATURES
    bar_range: float = 0.0
    ob_range: float = 0.0
    swing_range: float = 0.0
    distance_to_liquidity: float = 0.0
    volatility_regime: str = "UNKNOWN"
    # ------------------------------------------------------
    
    # Kontekst surowy (JSON w BigQuery)
    raw_context: Optional[Dict[str, Any]] = None
    
    # Pole techniczne dla bota (nie idzie do BQ)
    timestamp_raw: float = Field(alias='id_timestamp_raw', default=0.0)

    model_config = ConfigDict(
        populate_by_name=True,
        extra='ignore'
    )