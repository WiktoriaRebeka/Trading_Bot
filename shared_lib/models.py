from pydantic import BaseModel, Field, computed_field, ConfigDict
from typing import Optional
from datetime import datetime

class AlertData(BaseModel):
    # Mapowanie pól z Sierra Chart (id_...) na pola używane w logice bota
    symbol: str = Field(alias='id_symbol')
    direction: str = Field(alias='id_direction') # Oczekuje "LONG" lub "SHORT"
    entry: float
    sl: float
    tp: float
    timestamp_raw: float = Field(alias='id_timestamp_raw')
    risk_usdt: float = 2.5 

    model_config = ConfigDict(
        populate_by_name=True,
        extra='ignore'
    )

    @computed_field
    @property
    def direction(self) -> str:
        return "LONG" if self.direction_code == 1 else "SHORT"