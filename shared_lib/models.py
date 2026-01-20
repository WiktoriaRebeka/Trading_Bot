# Lokalizacja: shared_lib/models.py

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional

class AlertData(BaseModel):
    # Mapowanie pól bezpośrednio z Sierra Chart
    symbol: str = Field(alias='id_symbol')
    direction: str = Field(alias='id_direction')  # Sierra wysyła "LONG" lub "SHORT"
    entry: float
    sl: float
    tp: float
    timestamp_raw: float = Field(alias='id_timestamp_raw', default=0.0)
    risk_usdt: float = 2.5 

    model_config = ConfigDict(
        populate_by_name=True,
        extra='ignore'
    )
