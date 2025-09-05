# Lokalizacja: shared_lib/models.py


# Lokalizacja: shared_lib/models.py

from pydantic import BaseModel, Field, field_validator, ConfigDict, ValidationInfo
from typing import Optional, Dict, Any
from datetime import datetime, timezone

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

    model_config = ConfigDict(
        populate_by_name=True,
        extra='ignore'
    )

    @field_validator('direction', mode='before')
    @classmethod
    def set_direction_from_code(cls, v, info: ValidationInfo):
        code = None
        if 'directionCode' in info.data:
            code = info.data['directionCode']
        elif 'direction_code' in info.data:
            code = info.data['direction_code']

        if code is not None:
            if code == 1:
                return "LONG"
            if code == -1:
                return "SHORT"
        
        return "UNKNOWN"

class Kline(BaseModel):
    timestamp: int
    high: float
    low: float
    close: float

class AnalyticalCaseResults(BaseModel):
    tp_1_0: str = Field(default="UNRESOLVED")
    tp_1_5: str = Field(default="UNRESOLVED")
    tp_2_0: str = Field(default="UNRESOLVED")
    tp_3_0: str = Field(default="UNRESOLVED")
    tp_4_0: str = Field(default="UNRESOLVED")
    tp_5_0: str = Field(default="UNRESOLVED")

class AnalyticalCase(BaseModel):
    alert_id: str
    symbol: str
    status: str = Field(default="PENDING")
    alert_data: Dict[str, Any]
    triggered_at: Optional[datetime] = None
    results: AnalyticalCaseResults = Field(default_factory=AnalyticalCaseResults)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }