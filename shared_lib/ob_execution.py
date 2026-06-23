# shared_lib/ob_execution.py
# Entry / SL / sanity dla strategii MSI OrderBlock (współdzielone: orderflow_engine + bot_service).

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

logger = logging.getLogger(__name__)

MIN_OB_HEIGHT_PCT = 0.002  # 0.2% — poniżej opłaty round-trip (~0.075%) zjadają trade
ROUND_TRIP_FEE_PCT = 0.00075  # 0.075% — używane przy trailingu (krok 4)


@dataclass(frozen=True)
class ObTradeSetup:
    """Poziomy wejścia i SL wyliczone z granic strefy OrderBlock."""

    direction: str  # LONG | SHORT
    entry_limit: float
    sl: float
    risk_ob: float  # 1R = |entry − SL| = wysokość strefy OB
    ob_high: float
    ob_low: float
    ob_height: float
    ob_height_pct: float
    chain_id: str
    symbol: str


def _ob_bounds(ob: Any) -> Tuple[float, float]:
    """Pobiera HIGH/LOW z OrderBlock lub dict."""
    if hasattr(ob, "ob_high") and hasattr(ob, "ob_low"):
        return float(ob.ob_high), float(ob.ob_low)
    if isinstance(ob, dict):
        return float(ob["ob_high"]), float(ob["ob_low"])
    raise TypeError(f"Nieobsługiwany typ OB: {type(ob)}")


def _ob_direction(ob: Any) -> str:
    if hasattr(ob, "direction"):
        return str(ob.direction).upper()
    if isinstance(ob, dict):
        return str(ob["ob_direction"]).upper()
    raise TypeError(f"Nieobsługiwany typ OB: {type(ob)}")


def _ob_chain_id(ob: Any) -> str:
    if hasattr(ob, "chain_id"):
        return str(ob.chain_id)
    if isinstance(ob, dict):
        return str(ob.get("chain_id", ""))
    return ""


def _ob_symbol(ob: Any, fallback: str = "") -> str:
    if hasattr(ob, "symbol"):
        return str(ob.symbol).upper()
    if isinstance(ob, dict):
        return str(ob.get("symbol", fallback)).upper()
    return fallback.upper()


def compute_ob_entry_sl(ob: Any, *, symbol: str = "") -> ObTradeSetup:
    """
    Wylicza entry (Limit) i SL z granic strefy OB.

    LONG  (Higher LOW): entry = HIGH(OB), SL = LOW(OB)
    SHORT (Lower HIGH): entry = LOW(OB),  SL = HIGH(OB)
    """
    direction = _ob_direction(ob)
    ob_high, ob_low = _ob_bounds(ob)
    sym = _ob_symbol(ob, symbol)
    chain_id = _ob_chain_id(ob)

    if direction == "LONG":
        entry_limit = ob_high
        sl = ob_low
    elif direction == "SHORT":
        entry_limit = ob_low
        sl = ob_high
    else:
        raise ValueError(f"Nieobsługiwany kierunek OB: {direction}")

    ob_height = ob_high - ob_low
    risk_ob = abs(entry_limit - sl)
    ob_height_pct = (ob_height / entry_limit * 100.0) if entry_limit > 0 else 0.0

    return ObTradeSetup(
        direction=direction,
        entry_limit=entry_limit,
        sl=sl,
        risk_ob=risk_ob,
        ob_high=ob_high,
        ob_low=ob_low,
        ob_height=ob_height,
        ob_height_pct=ob_height_pct,
        chain_id=chain_id,
        symbol=sym,
    )


def validate_ob_sanity(setup: ObTradeSetup) -> Tuple[bool, str]:
    """
    Obowiązkowy sanity check (orderflow_engine ORAZ bot_service).

    SHORT: SL > entry.  LONG: SL < entry.  R > 0.
  """
    if setup.risk_ob <= 0:
        return False, f"risk_ob={setup.risk_ob} <= 0"

    if setup.direction == "LONG":
        if setup.sl >= setup.entry_limit:
            return (
                False,
                f"LONG sanity fail: SL ({setup.sl}) >= entry ({setup.entry_limit})",
            )
    elif setup.direction == "SHORT":
        if setup.sl <= setup.entry_limit:
            return (
                False,
                f"SHORT sanity fail: SL ({setup.sl}) <= entry ({setup.entry_limit})",
            )
    else:
        return False, f"Nieznany kierunek: {setup.direction}"

    return True, "ok"


def passes_min_ob_height(
    setup: ObTradeSetup,
    min_pct: float = MIN_OB_HEIGHT_PCT,
) -> Tuple[bool, str]:
    """Odrzuć OB gdy (HIGH−LOW)/entry × 100 < min_pct (domyślnie 0.2%)."""
    threshold_pct = min_pct * 100.0
    if setup.ob_height_pct < threshold_pct:
        return (
            False,
            f"OB za ciasny: {setup.ob_height_pct:.4f}% < {threshold_pct:.2f}% "
            f"(height={setup.ob_height:.8f}, entry={setup.entry_limit})",
        )
    return True, "ok"


def build_ob_trade_setup(
    ob: Any,
    *,
    symbol: str = "",
    min_height_pct: float = MIN_OB_HEIGHT_PCT,
    log_prefix: str = "",
) -> Optional[ObTradeSetup]:
    """
    Pełna ścieżka: wylicz entry/SL → sanity → filtr wysokości.
    Zwraca None + loguje powód przy odrzuceniu.
    """
    prefix = f"{log_prefix} " if log_prefix else ""
    try:
        setup = compute_ob_entry_sl(ob, symbol=symbol)
    except (TypeError, ValueError, KeyError) as e:
        logger.error("%sOB setup compute failed: %s", prefix, e)
        return None

    ok, reason = validate_ob_sanity(setup)
    if not ok:
        logger.warning(
            "%sOB REJECTED sanity [%s %s chain=%s]: %s",
            prefix,
            setup.symbol,
            setup.direction,
            setup.chain_id,
            reason,
        )
        return None

    ok, reason = passes_min_ob_height(setup, min_height_pct)
    if not ok:
        logger.debug(
            "%sOB REJECTED min_height [%s %s chain=%s]: %s",
            prefix,
            setup.symbol,
            setup.direction,
            setup.chain_id,
            reason,
        )
        return None

    logger.debug(
        "%sOB ACCEPTED [%s %s chain=%s] entry=%s sl=%s R=%s height_pct=%.4f%%",
        prefix,
        setup.symbol,
        setup.direction,
        setup.chain_id,
        setup.entry_limit,
        setup.sl,
        setup.risk_ob,
        setup.ob_height_pct,
    )
    return setup


def setup_from_levels(
    direction: str,
    entry_limit: float,
    sl: float,
    *,
    chain_id: str = "",
    symbol: str = "",
    ob_high: Optional[float] = None,
    ob_low: Optional[float] = None,
) -> ObTradeSetup:
    """Buduje ObTradeSetup z już znanych poziomów (np. po zaokrągleniu tick w bot_service)."""
    direction_u = str(direction).upper()
    hi = float(ob_high if ob_high is not None else max(entry_limit, sl))
    lo = float(ob_low if ob_low is not None else min(entry_limit, sl))
    height = hi - lo
    risk = abs(float(entry_limit) - float(sl))
    entry_f = float(entry_limit)
    height_pct = (height / entry_f * 100.0) if entry_f > 0 else 0.0
    return ObTradeSetup(
        direction=direction_u,
        entry_limit=entry_f,
        sl=float(sl),
        risk_ob=risk,
        ob_high=hi,
        ob_low=lo,
        ob_height=height,
        ob_height_pct=height_pct,
        chain_id=str(chain_id),
        symbol=str(symbol).upper(),
    )


def resolve_trailing_r(
    data: Dict[str, Any],
    real_entry: float,
    planned_sl: float,
) -> float:
    """
    1R do trailingu.
    MSI OrderBlock: risk_ob ze strefy OB (|entry−SL| z limitem).
    Footprint: |real_entry − planned_sl| po fillu.
    """
    if str(data.get("signal_mode", "")).lower() == "msi_orderblock":
        raw = data.get("risk_ob")
        if raw is not None:
            r = float(raw)
            if r > 0:
                return r
    return abs(float(real_entry) - float(planned_sl))


def compute_trailing_levels(
    direction: str,
    real_entry: float,
    risk_r: float,
    *,
    fee_adjusted: bool = False,
) -> tuple[float, float]:
    """
    Zwraca (raw_active_price, trailing_distance=1R).

    MSI (fee_adjusted=True):
      LONG  active = entry + 2R + 0.075%×entry
      SHORT active = entry − 2R − 0.075%×entry
    Footprint (fee_adjusted=False):
      LONG  active = entry + 2R
      SHORT active = entry − 2R
    """
    if risk_r <= 0 or real_entry <= 0:
        return 0.0, 0.0

    fee_adj = real_entry * ROUND_TRIP_FEE_PCT if fee_adjusted else 0.0
    direction_u = str(direction).upper()
    if direction_u == "LONG":
        raw_active = real_entry + 2 * risk_r + fee_adj
    elif direction_u == "SHORT":
        raw_active = real_entry - 2 * risk_r - fee_adj
    else:
        return 0.0, 0.0
    return raw_active, risk_r
