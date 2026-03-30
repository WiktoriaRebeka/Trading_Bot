# ============================================
# signal_detector.py — Institutional Footprint Hunter (Production)
# ============================================

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional
from datetime import datetime
import logging
import uuid

logger = logging.getLogger(__name__)

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
    symbol: str
    direction: Direction
    current_price: float
    swing_point: SwingPoint
    liquidations: List[Dict[str, Any]]
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
                       lookback_s: int = 300,
                       min_volume_usd: float = 50_000.0) -> bool:
    MAX_LIQUIDATION_AGE_SECONDS = float(lookback_s)

    # Bybit: S=Buy → likwidacja longa; S=Sell → likwidacja shorta (docs v5 allLiquidation)
    if ctx.direction == "LONG":
        target_sides = {"buy", "Buy", "BUY"}
    else:
        target_sides = {"sell", "Sell", "SELL"}

    now_utc = datetime.utcnow()
    vol = 0.0
    symbol = ctx.symbol

    for liquidation_data in ctx.liquidations:
        liq_time_ms = int(liquidation_data.get("T") or liquidation_data.get("time") or 0)
        event_id = f"liq_{symbol}_{liq_time_ms}"

        raw_side = str(liquidation_data.get("side") or "")
        if raw_side not in target_sides:
            logger.debug(
                f"[{event_id}] check_liquidations: skip — side mismatch (want {target_sides})"
            )
            continue

        liq_time = datetime.utcfromtimestamp(liq_time_ms / 1000.0)
        age_seconds = (now_utc - liq_time).total_seconds()

        if age_seconds > MAX_LIQUIDATION_AGE_SECONDS:
            logger.info(
                f"[{event_id}] check_liquidations: excluded — age_seconds={age_seconds:.3f} "
                f"> MAX_LIQUIDATION_AGE_SECONDS={MAX_LIQUIDATION_AGE_SECONDS}"
            )
            continue

        vol += float(liquidation_data.get("volume_usd", 0.0))

    ok = vol >= min_volume_usd
    summary_eid = f"liq_{symbol}_check_{int(now_utc.timestamp() * 1000)}"
    if not ok:
        logger.info(
            f"[{summary_eid}] check_liquidations: volume_usd={vol:.2f} < min_volume_usd={min_volume_usd}"
        )
    else:
        logger.info(
            f"[{summary_eid}] check_liquidations: pass — volume_usd={vol:.2f} >= min_volume_usd={min_volume_usd}"
        )
    return ok


# ============================================================
# === 3. DELTA DIVERGENCE
# ============================================================

def check_delta_divergence(ctx: SignalContext) -> bool:
    """
    LONG:
      - cena robi LL względem min z ostatnich 9 świec (okno -10:-1)
      - delta rośnie względem min delty w tym samym oknie (bullish divergence)
    SHORT:
      - cena robi HH względem max z ostatnich 9 świec
      - delta spada względem max delty (bearish divergence)
    """
    if len(ctx.recent_deltas) < 10:
        return False

    recent = ctx.recent_deltas[-30:] if len(ctx.recent_deltas) >= 30 else ctx.recent_deltas
    prices = [d.price for d in recent]
    deltas = [d.delta for d in recent]

    if ctx.direction == "LONG":
        # Cena robi nowe minimum, delta rośnie (bullish divergence)
        price_new_low = prices[-1] < min(prices[-10:-1])
        delta_rising = deltas[-1] > min(deltas[-10:-1])
        result = price_new_low and delta_rising
    else:  # SHORT
        # Cena robi nowe maksimum, delta spada (bearish divergence)
        price_new_high = prices[-1] > max(prices[-10:-1])
        delta_falling = deltas[-1] < max(deltas[-10:-1])
        result = price_new_high and delta_falling

    if not result:
        logger.debug(
            f"[{ctx.symbol}] check_delta_divergence: False — "
            f"direction={ctx.direction}, samples={len(recent)}, "
            f"last_price={prices[-1]:.4f}, last_delta={deltas[-1]:.2f}"
        )
    return result


# ============================================================
# === 4. DOM WALL / OBI
# ============================================================

def check_dom_wall(ctx: SignalContext,
                   min_obi_long: float = 0.4,
                   max_obi_short: float = -0.4,
                   depth_levels: int = 10,
                   min_wall_multiplier: float = 2.0) -> bool:

    bids = ctx.dom_snapshot.bids[:depth_levels]
    asks = ctx.dom_snapshot.asks[:depth_levels]

    sum_bids = sum(size for _, size in bids)
    sum_asks = sum(size for _, size in asks)

    if ctx.direction == "LONG":
        if ctx.dom_snapshot.obi < min_obi_long:
            logger.debug(
                f"[{ctx.symbol}] check_dom_wall: False — OBI={ctx.dom_snapshot.obi:.3f} < {min_obi_long}"
            )
            return False
        return sum_bids >= min_wall_multiplier * sum_asks

    else:  # SHORT
        if ctx.dom_snapshot.obi > max_obi_short:
            logger.debug(
                f"[{ctx.symbol}] check_dom_wall: False — OBI={ctx.dom_snapshot.obi:.3f} > {max_obi_short}"
            )
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
            "liquidations": list(ctx.liquidations),
            "deltas": [d.__dict__ for d in ctx.recent_deltas],
            "obi": ctx.dom_snapshot.obi,
            "funding_rate": ctx.funding_rate
        }
    }