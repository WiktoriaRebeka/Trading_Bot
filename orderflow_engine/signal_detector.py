# ============================================
# signal_detector.py — Institutional Footprint Hunter (Production)
# ============================================

from dataclasses import dataclass
from typing import List, Literal, Optional
from datetime import datetime, timedelta
import uuid

Direction = Literal["LONG", "SHORT"]


# ============================================================
# === DATA MODELS
# ============================================================

@dataclass
class SwingPoint:
    price: float
    timestamp: datetime


@dataclass
class LiquidationEvent:
    side: Literal["LONG", "SHORT"]
    volume_usd: float
    timestamp: datetime


@dataclass
class DeltaPoint:
    price: float
    delta: float
    timestamp: datetime


@dataclass
class DomSnapshot:
    bids: List[tuple]   # [(price, size), ...]
    asks: List[tuple]   # [(price, size), ...]
    obi: float          # -1 .. +1


@dataclass
class SignalContext:
    direction: Direction
    current_price: float
    swing_point: SwingPoint
    liquidations: List[LiquidationEvent]
    recent_deltas: List[DeltaPoint]
    dom_snapshot: DomSnapshot
    funding_rate: float


# ============================================================
# === 1. LIQUIDITY SWEEP
# ============================================================

def detect_liquidity_sweep(ctx: SignalContext) -> bool:
    """
    LONG: cena musi przebić Swing Low
    SHORT: cena musi przebić Swing High
    """
    if ctx.direction == "LONG":
        return ctx.current_price < ctx.swing_point.price
    else:
        return ctx.current_price > ctx.swing_point.price


# ============================================================
# === 2. LIQUIDATIONS
# ============================================================

def check_liquidations(ctx: SignalContext,
                       lookback_s: int = 60,
                       min_volume_usd: float = 50_000.0) -> bool:

    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=lookback_s)

    if ctx.direction == "LONG":
        side = "LONG"
    else:
        side = "SHORT"

    vol = sum(
        l.volume_usd
        for l in ctx.liquidations
        if l.side == side and l.timestamp >= cutoff
    )

    return vol >= min_volume_usd


# ============================================================
# === 3. DELTA DIVERGENCE
# ============================================================

def check_delta_divergence(ctx: SignalContext) -> bool:
    """
    LONG:
      - cena robi LL
      - delta robi HL
    SHORT:
      - cena robi HH
      - delta robi LH
    """
    if len(ctx.recent_deltas) < 2:
        return False

    last = ctx.recent_deltas[-1]
    prev = ctx.recent_deltas[-2]

    if ctx.direction == "LONG":
        return last.price < prev.price and last.delta > prev.delta

    else:  # SHORT
        return last.price > prev.price and last.delta < prev.delta


# ============================================================
# === 4. DOM WALL / OBI
# ============================================================

def check_dom_wall(ctx: SignalContext,
                   min_obi_long: float = 0.3,
                   max_obi_short: float = -0.3,
                   depth_levels: int = 10,
                   min_wall_multiplier: float = 2.0) -> bool:

    bids = ctx.dom_snapshot.bids[:depth_levels]
    asks = ctx.dom_snapshot.asks[:depth_levels]

    sum_bids = sum(size for _, size in bids)
    sum_asks = sum(size for _, size in asks)

    if ctx.direction == "LONG":
        if ctx.dom_snapshot.obi < min_obi_long:
            return False
        return sum_bids >= min_wall_multiplier * sum_asks

    else:  # SHORT
        if ctx.dom_snapshot.obi > max_obi_short:
            return False
        return sum_asks >= min_wall_multiplier * sum_bids


# ============================================================
# === 5. CONFIDENCE SCORE
# ============================================================

def compute_confidence_score(ctx: SignalContext,
                             liq_ok: bool,
                             delta_ok: bool,
                             dom_ok: bool,
                             structure_quality: float = 1.0) -> float:

    score = 0.0

    # Liquidations (0–30)
    score += 30.0 if liq_ok else 0.0

    # Delta divergence (0–25)
    score += 25.0 if delta_ok else 0.0

    # DOM / OBI (0–20)
    score += 20.0 if dom_ok else 0.0

    # Structure quality (0–15)
    structure_quality = max(0.0, min(1.0, structure_quality))
    score += 15.0 * structure_quality

    # Funding contrarian bonus (0–10)
    fr = ctx.funding_rate
    if ctx.direction == "LONG" and fr > 0:
        score += 10.0
    elif ctx.direction == "SHORT" and fr < 0:
        score += 10.0

    return score


# ============================================================
# === 6. FINAL DECISION
# ============================================================

def should_generate_alert(ctx: SignalContext,
                          min_confidence: float = 70.0) -> bool:
    """
    Implementacja Institutional Footprint Hunter:
    - Sweep
    - Liquidations
    - Delta Divergence
    - DOM Wall
    - Confidence Score >= 70
    """

    # Sweep
    if not detect_liquidity_sweep(ctx):
        return False

    # Liquidations
    liq_ok = check_liquidations(ctx)

    # Delta divergence
    delta_ok = check_delta_divergence(ctx)

    # DOM wall
    dom_ok = check_dom_wall(ctx)

    # Required conditions
    if not (liq_ok and delta_ok and dom_ok):
        return False

    # Confidence score
    score = compute_confidence_score(ctx, liq_ok, delta_ok, dom_ok)
    return score >= min_confidence


# ============================================================
# === 7. ALERT PAYLOAD BUILDER
# ============================================================

def build_alert_payload(symbol: str,
                        direction: Direction,
                        entry: float,
                        sl: float,
                        tp: float,
                        ctx: SignalContext) -> dict:

    return {
        "event_id": f"{symbol}-{uuid.uuid4().hex[:12]}",
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk_usdt": 10,
        "rr": 3.0,
        "structure_state": "SWEEP",
        "raw_context": {
            "sweep_price": ctx.swing_point.price,
            "liquidations": [l.__dict__ for l in ctx.liquidations],
            "deltas": [d.__dict__ for d in ctx.recent_deltas],
            "obi": ctx.dom_snapshot.obi,
            "funding_rate": ctx.funding_rate
        }
    }