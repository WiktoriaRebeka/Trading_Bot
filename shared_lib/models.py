# Lokalizacja: shared_lib/models.py
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, Dict, Any

class AlertData(BaseModel):
    """
    Model danych dla sygnału tradingowego z Sierra Chart.
    
    ZMIANY W WERSJI 2.0:
    - Usunięto alias 'id_symbol' - teraz oczekujemy 'symbol' bezpośrednio
    - raw_context jako Dict (nie string)
    - Dodano walidację event_id
    """
    
    # === IDENTYFIKATORY ===
    signal_id: str
    event_id: str  # PRIMARY KEY - unikalny identyfikator zdarzenia
    
    # === PODSTAWOWE DANE ===
    symbol: str  # BEZ ALIASU - Sierra wysyła 'symbol' (np. "BTCUSDT")
    timestamp: str  # STRING ISO 8601 (np. "2024-01-31T14:30:00Z")
    direction: str  # "LONG" lub "SHORT"
    
    # === CENY I RYZYKO ===
    entry: float
    sl: float
    tp: float
    risk_pct: float
    rr: float
    
    # === STRUKTURA RYNKU ===
    structure_state: int  # +1 bullish, -1 bearish
    
    # === ZDARZENIA BOS/CHOCH (NULLABLE) ===
    bos_high: Optional[bool] = False
    bos_low: Optional[bool] = False
    choch_up: Optional[bool] = False
    choch_down: Optional[bool] = False
    
    # === LIQUIDITY (NULLABLE) ===
    liquidity_grab_above: Optional[bool] = False
    liquidity_grab_below: Optional[bool] = False
    liquidity_price: Optional[float] = None
    eqh_detected: Optional[bool] = False
    eql_detected: Optional[bool] = False
    
    # === ANALITYKA (OrderFlow + Risk) ===
    risk_usdt: float = 0.0
    m2_delta: float = 0.0
    m5_rs_ratio: float = 0.0
    
    # === TIME FEATURES (Etap 1) ===
    session: str = "UNKNOWN"
    minute_of_day: int = -1
    day_of_week: int = -1
    second: int = -1
    
    # === VOLATILITY FEATURES (Etap 2) ===
    bar_range: float = 0.0
    ob_range: float = 0.0
    swing_range: float = 0.0
    distance_to_liquidity: float = 0.0
    volatility_regime: str = "UNKNOWN"
    
    # === RAW CONTEXT (JSON w BigQuery) ===
    raw_context: Optional[Dict[str, Any]] = None  # Dict, NIE string
    
    # === WALIDATORY ===
    @field_validator('event_id')
    @classmethod
    def validate_event_id(cls, v: str) -> str:
        """Sprawdza, czy event_id nie jest pusty i mieści się w limicie Bybit."""
        if not v or len(v.strip()) == 0:
            raise ValueError("event_id nie może być pusty")
        
        if len(v) > 36:
            raise ValueError(f"event_id za długi ({len(v)} znaków, max 36)")
        
        return v.strip()
    
    @field_validator('direction')
    @classmethod
    def validate_direction(cls, v: str) -> str:
        """Normalizuje direction do LONG/SHORT."""
        v_upper = v.upper()
        if v_upper not in ["LONG", "SHORT"]:
            raise ValueError(f"direction musi być LONG lub SHORT, otrzymano: {v}")
        return v_upper
    
    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        """Waliduje format symbolu."""
        v_clean = v.upper().strip()
        
        if len(v_clean) < 3:
            raise ValueError(f"symbol za krótki: {v}")
        
        # Usuń .P jeśli C++ by wysłał (dla kompatybilności)
        v_clean = v_clean.replace('.P', '')
        
        return v_clean
    
    model_config = ConfigDict(
        populate_by_name=True,
        extra='ignore',  # Ignoruj dodatkowe pola (np. secret_token)
        str_strip_whitespace=True
    )