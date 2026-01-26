# Lokalizacja: shared_lib/models.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any

class AlertData(BaseModel):
    # Mapowanie pól z JSONa Sierry na model Pythona
    signal_id: str
    symbol: str = Field(alias='id_symbol')
    timestamp: str
    direction: str
    entry: float
    sl: float
    tp: float
    risk_pct: float
    rr: float
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
    
    # Kontekst surowy (JSON w BigQuery)
    raw_context: Optional[Dict[str, Any]] = None
    
    # Pole techniczne dla bota (nie idzie do BQ)
    timestamp_raw: float = Field(alias='id_timestamp_raw', default=0.0)

    model_config = ConfigDict(
        populate_by_name=True,
        extra='ignore'
    )
