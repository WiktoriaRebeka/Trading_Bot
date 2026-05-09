import logging
from dataclasses import dataclass
from typing import Any, Optional

SWING_BUFFER_PCT = 0.003
MAKER_FEE = 0.0002  # 0.02%
TAKER_FEE = 0.00055  # 0.055%
MIN_SL_DISTANCE_PCT = 0.002  # 0.2% minimum SL distance vs entry
FALLBACK_SL_PCT = 0.01
TP_SWING_CAP_BUFFER_PCT = 0.001
RR_THRESHOLD_RATIO = 0.9  # Allow 10% reduction below target after capping


def _target_net_rr_for_confidence(confidence: float) -> float:
    """Pick target net RR (after fees) based on signal confidence score."""
    if confidence >= 85:
        return 1.0
    if confidence >= 75:
        return 1.5
    return 2.0


@dataclass(frozen=True)
class StructureRiskLevels:
    sl: float
    tp: float
    risk_pct: float
    rr: float
    swing_level: float
    fallback_used: bool = False
    tp_capped: bool = False
    net_sl_risk: float = 0.0


def calculate_structure_risk_levels(
    symbol: str,
    direction: str,
    entry_price: float,
    engine: Any,
    confidence: float,
    logger: logging.Logger,
) -> Optional[StructureRiskLevels]:
    """Calculate SL/TP from the swept swing invalidation level (fee-adjusted net RR)."""
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

    target_net_rr = _target_net_rr_for_confidence(confidence)
    logger.info(
        f"📊 {sym} Confidence {confidence:.0f}% → Target net RR {target_net_rr:.1f}"
    )

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

        sl_distance = abs(entry - sl_price)
        min_sl_distance = entry * MIN_SL_DISTANCE_PCT
        if sl_distance < min_sl_distance:
            logger.warning(
                f"⚠️ {sym} SL too tight ({sl_distance / entry * 100:.2f}%), "
                f"expanding to {MIN_SL_DISTANCE_PCT * 100:.1f}%"
            )
            sl_distance = min_sl_distance
            sl_price = entry - sl_distance

        entry_fee = entry * MAKER_FEE
        sl_exit_fee = sl_price * TAKER_FEE
        net_sl_risk = sl_distance + entry_fee + sl_exit_fee

        tp_exit_fee = entry * MAKER_FEE
        net_profit_needed = net_sl_risk * target_net_rr
        gross_tp_distance = net_profit_needed + entry_fee + tp_exit_fee

        tp_price = entry + gross_tp_distance
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

        tp_distance_final = abs(tp_price - entry)

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

        sl_distance = abs(entry - sl_price)
        min_sl_distance = entry * MIN_SL_DISTANCE_PCT
        if sl_distance < min_sl_distance:
            logger.warning(
                f"⚠️ {sym} SL too tight ({sl_distance / entry * 100:.2f}%), "
                f"expanding to {MIN_SL_DISTANCE_PCT * 100:.1f}%"
            )
            sl_distance = min_sl_distance
            sl_price = entry + sl_distance

        entry_fee = entry * MAKER_FEE
        sl_exit_fee = sl_price * TAKER_FEE
        net_sl_risk = sl_distance + entry_fee + sl_exit_fee

        tp_exit_fee = entry * MAKER_FEE
        net_profit_needed = net_sl_risk * target_net_rr
        gross_tp_distance = net_profit_needed + entry_fee + tp_exit_fee

        tp_price = entry - gross_tp_distance
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

        tp_distance_final = abs(tp_price - entry)

    else:
        logger.error(f"❌ {sym} unsupported direction for structure-based SL: {direction}")
        return None

    if sl_distance <= 0 or tp_distance_final <= 0:
        logger.error(
            f"❌ {sym} {side} invalid structure risk distances: "
            f"sl_distance={sl_distance:.8f} tp_distance_final={tp_distance_final:.8f}"
        )
        return None

    entry_fee = entry * MAKER_FEE
    tp_exit_fee = entry * MAKER_FEE
    net_profit_final = tp_distance_final - entry_fee - tp_exit_fee
    actual_net_rr = net_profit_final / net_sl_risk

    min_rr_threshold = target_net_rr * RR_THRESHOLD_RATIO
    if actual_net_rr < min_rr_threshold:
        logger.warning(
            f"❌ {sym} {side} Net RR {actual_net_rr:.2f} < threshold {min_rr_threshold:.2f} "
            f"(target {target_net_rr:.1f}, conf {confidence:.0f}%). SKIP. "
            f"(entry={entry:.4f}, sl={sl_price:.4f}, tp={tp_price:.4f}, "
            f"net_sl_risk={net_sl_risk:.8f}, tp_capped={tp_capped})"
        )
        return None

    risk_pct = (net_sl_risk / entry) * 100
    tp_distance_pct = (tp_distance_final / entry) * 100

    logger.warning(f"🎯 {sym} {side} STRUCTURE-BASED SETUP (fee-adjusted):")
    logger.warning(f"   Swept swing level: {float(swing_level):.4f}")
    logger.warning(f"   Entry: {entry:.4f}")
    logger.warning(f"   SL: {sl_price:.4f} (net risk {risk_pct:.4f}% incl. fees)")
    logger.warning(f"   TP: {tp_price:.4f} ({tp_distance_pct:.2f}% price move)")
    logger.warning(f"   Net SL risk (price+fees): {net_sl_risk:.8f}")
    logger.warning(f"   Confidence: {confidence:.1f}/100  Target RR: {target_net_rr:.1f}")
    logger.warning(f"✅ {sym} Net RR after fees: {actual_net_rr:.2f}")

    return StructureRiskLevels(
        sl=sl_price,
        tp=tp_price,
        risk_pct=risk_pct,
        rr=actual_net_rr,
        swing_level=float(swing_level),
        fallback_used=fallback_used,
        tp_capped=tp_capped,
        net_sl_risk=net_sl_risk,
    )
