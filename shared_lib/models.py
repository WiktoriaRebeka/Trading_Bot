from pydantic import BaseModel, Field, computed_field, ConfigDict
from typing import Optional
from datetime import datetime

class AlertData(BaseModel):
    symbol: str
    direction_code: int = Field(alias='directionCode')
    entry: float
    sl: float
    tp: float
    timestamp: str 
    risk_usdt: float = 2.5 # Twoje stałe ryzyko

    model_config = ConfigDict(
        populate_by_name=True,
        extra='ignore'
    )

    @computed_field
    @property
    def direction(self) -> str:
        return "LONG" if self.direction_code == 1 else "SHORT"