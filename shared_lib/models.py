# Lokalizacja: shared_lib/models.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional

class AlertData(BaseModel):
    symbol: str = Field(alias='id_symbol')
    direction: str = Field(alias='id_direction')
    entry: float
    sl: float
    tp: float
    # NOWE POLE:
    m2_delta: float = Field(alias='m2_delta', default=0.0) 
    
    timestamp_raw: float = Field(alias='id_timestamp_raw', default=0.0)
    risk_usdt: float = 2.5

    model_config = ConfigDict(
        populate_by_name=True,
        extra='ignore'
    )
