import logging
from dataclasses import dataclass
from typing import Any, Optional

SWING_BUFFER_PCT = 0.003
RR_TARGET = 2.0
FALLBACK_SL_PCT = 0.01
TP_SWING_CAP_BUFFER_PCT = 0.001


@dataclass(frozen=True)
class StructureRiskLevels:
    sl: float
    tp: float
    risk_pct: float
    rr: float
    swing_level: float
    fallback_used: bool = False
    tp_capped: bool = False


def calculate_structure_risk_levels(
    symbol: str,
    direction: str,
    entry_price: float,
    engine: Any,
    confidence: float,
    logger: logging.Logger,
) -> Optional[StructureRiskLevels]:
    """Calculate SL/TP from the swept swing invalidation level."""
    sym = str(symbol).upper()
    side = str(direction).upper()

    try:
        entry = float(entry_price)
    except (TypeError, ValueError):
        logger.error(f"❌ {sym} {side} invalid entry price for structure-based SL: {entry_price}")
        return None

    if entry <= 0:
        logger.error(f"❌ {sym} {side} invalid entry price for structure-based SL: {entry}")
        return None

    if side == "LONG":
        swing_level = getattr(engine, "last_swing_low", None)
        if not swing_level or swing_level <= 0:
            logger.error(f"❌ {sym} No valid swing low - cannot set structure-based SL")
            return None

        sl_price = float(swing_level) - (float(swing_level) * SWING_BUFFER_PCT)
        fallback_used = False
        if sl_price >= entry:
            sl_price = entry * (1 - FALLBACK_SL_PCT)
            fallback_used = True
            logger.warning(
                f"⚠️ {sym} LONG structure SL invalid at/above entry; "
                f"using fallback SL {sl_price:.4f}"
            )

        sl_distance = entry - sl_price
        tp_price = entry + (sl_distance * RR_TARGET)
        tp_capped = False
        swing_target = getattr(engine, "last_swing_high", None)
        if swing_target and swing_target > entry and tp_price > swing_target:
            capped_tp = float(swing_target) - (float(swing_target) * TP_SWING_CAP_BUFFER_PCT)
            if capped_tp > entry:
                tp_price = capped_tp
                tp_capped = True
                logger.info(f"📍 {sym} TP capped at swing high: {tp_price:.4f}")
            else:
                logger.warning(f"⚠️ {sym} LONG swing-high cap ignored because it is not above entry")

        tp_distance = tp_price - entry

    elif side == "SHORT":
        swing_level = getattr(engine, "last_swing_high", None)
        if not swing_level or swing_level <= 0:
            logger.error(f"❌ {sym} No valid swing high - cannot set structure-based SL")
            return None

        sl_price = float(swing_level) + (float(swing_level) * SWING_BUFFER_PCT)
        fallback_used = False
        if sl_price <= entry:
            sl_price = entry * (1 + FALLBACK_SL_PCT)
            fallback_used = True
            logger.warning(
                f"⚠️ {sym} SHORT structure SL invalid at/below entry; "
                f"using fallback SL {sl_price:.4f}"
            )

        sl_distance = sl_price - entry
        tp_price = entry - (sl_distance * RR_TARGET)
        tp_capped = False
        swing_target = getattr(engine, "last_swing_low", None)
        if swing_target and swing_target < entry and tp_price < swing_target:
            capped_tp = float(swing_target) + (float(swing_target) * TP_SWING_CAP_BUFFER_PCT)
            if capped_tp < entry:
                tp_price = capped_tp
                tp_capped = True
                logger.info(f"📍 {sym} TP capped at swing low: {tp_price:.4f}")
            else:
                logger.warning(f"⚠️ {sym} SHORT swing-low cap ignored because it is not below entry")

        tp_distance = entry - tp_price

    else:
        logger.error(f"❌ {sym} unsupported direction for structure-based SL: {direction}")
        return None

    if sl_distance <= 0 or tp_distance <= 0:
        logger.error(
            f"❌ {sym} {side} invalid structure risk distances: "
            f"sl_distance={sl_distance:.8f} tp_distance={tp_distance:.8f}"
        )
        return None

    risk_pct = (sl_distance / entry) * 100
    tp_distance_pct = (tp_distance / entry) * 100
    actual_rr = tp_distance / sl_distance

    logger.warning(f"🎯 {sym} {side} STRUCTURE-BASED SETUP:")
    logger.warning(f"   Swept swing level: {float(swing_level):.4f}")
    logger.warning(f"   Entry: {entry:.4f}")
    logger.warning(f"   SL: {sl_price:.4f} ({risk_pct:.2f}%)")
    logger.warning(f"   TP: {tp_price:.4f} ({tp_distance_pct:.2f}%)")
    logger.warning(f"   Actual RR: {actual_rr:.2f}")
    logger.warning(f"   Confidence: {confidence:.1f}/100")

    return StructureRiskLevels(
        sl=sl_price,
        tp=tp_price,
        risk_pct=risk_pct,
        rr=actual_rr,
        swing_level=float(swing_level),
        fallback_used=fallback_used,
        tp_capped=tp_capped,
    )
