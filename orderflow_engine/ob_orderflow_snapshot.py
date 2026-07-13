# orderflow_engine/ob_orderflow_snapshot.py
# Cechy orderflow + Volume Profile z API (bez bufora w pamięci).

from __future__ import annotations

import os
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --- Volume Profile (log only, bez gate'u wejścia) ---
VP_PERIOD_SHORT = int(os.environ.get("VP_PERIOD_SHORT", "300"))
VP_PERIOD_LONG = int(os.environ.get("VP_PERIOD_LONG", "1440"))
VP_BINS_SHORT = int(os.environ.get("VP_BINS_SHORT", os.environ.get("VP_BINS", "70")))
VP_BINS_LONG = int(os.environ.get("VP_BINS_LONG", os.environ.get("VP_BINS", "70")))
VP_MIN_CANDLES = int(os.environ.get("VP_MIN_CANDLES", "30"))
VA_VOLUME_PCT = float(os.environ.get("VP_VA_VOLUME_PCT", "0.70"))

_HORIZON_PREFIXES = {
    "short": "vp5h_",
    "long": "vp24h_",
}


def collect_ob_orderflow_features(
    processor: Any,
    symbol: str,
    direction: str,
) -> Dict[str, Optional[float | bool]]:
    """matched_liq_volume, obi, funding_rate, dom_wall, delta — tylko do analizy."""
    sym = str(symbol).upper().replace(".P", "")
    out: Dict[str, Optional[float | bool]] = {
        "matched_liq_volume": None,
        "obi": None,
        "funding_rate": None,
        "dom_wall": None,
        "delta": None,
    }
    if processor is None:
        return out

    try:
        if hasattr(processor, "_matched_liquidation_buffer_usd"):
            out["matched_liq_volume"] = float(
                processor._matched_liquidation_buffer_usd(sym, str(direction).upper())
            )
    except Exception:
        pass

    try:
        snap = processor.get_dom_snapshot(sym) if hasattr(processor, "get_dom_snapshot") else {}
        if snap:
            obi = snap.get("obi")
            if obi is not None:
                out["obi"] = float(obi)
            walls = (snap.get("bid_walls") or []) + (snap.get("ask_walls") or [])
            out["dom_wall"] = bool(walls)
    except Exception:
        pass

    try:
        ticker = getattr(processor, "tickers", {}).get(sym) or {}
        fr = ticker.get("funding_rate")
        if fr is not None:
            out["funding_rate"] = float(fr)
    except Exception:
        pass

    try:
        if hasattr(processor, "get_recent_deltas"):
            deltas = processor.get_recent_deltas(sym, limit=1)
            if deltas:
                d = deltas[-1].get("delta")
                if d is not None:
                    out["delta"] = float(d)
    except Exception:
        pass

    return out


def normalize_vol_candle(c: dict) -> Dict[str, Any]:
    return {
        "ts": int(c["ts"]),
        "low": float(c["low"]),
        "high": float(c["high"]),
        "volume": float(c.get("volume", 0.0) or 0.0),
    }


def _empty_horizon_fields(prefix: str, vp_candles_used: int = 0) -> Dict[str, Any]:
    return {
        f"{prefix}poc_price": None,
        f"{prefix}va_high": None,
        f"{prefix}va_low": None,
        f"{prefix}ob_vp_ratio": None,
        f"{prefix}ob_outside_va": False,
        f"{prefix}ob_dist_from_poc_pct": None,
        f"{prefix}sl_vp_ratio": None,
        f"{prefix}entry_vp_ratio": None,
        f"{prefix}sl_outside_va": False,
        f"{prefix}entry_outside_va": False,
        f"{prefix}vp_candles_used": vp_candles_used,
        f"{prefix}vp_reliable": False,
        f"{prefix}vp_median_bin_vol": None,
        f"{prefix}ob_zone_bin_vol_avg": None,
        f"{prefix}sl_bin_vol_avg": None,
        f"{prefix}entry_bin_vol_avg": None,
        f"{prefix}vp_bin_width": None,
        f"{prefix}vp_price_min": None,
        f"{prefix}vp_price_max": None,
        f"{prefix}ob_zone_bins_covered": 0,
    }


def empty_dual_vp_context() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "vp_bins_short": VP_BINS_SHORT,
        "vp_bins_long": VP_BINS_LONG,
    }
    out.update(_empty_horizon_fields(_HORIZON_PREFIXES["short"]))
    out.update(_empty_horizon_fields(_HORIZON_PREFIXES["long"]))
    return out


def _price_to_bin_idx(price: float, p_min: float, bin_w: float, n_bins: int) -> int:
    if bin_w <= 0.0:
        return 0
    i = int((price - p_min) / bin_w)
    if i < 0:
        return 0
    if i > n_bins - 1:
        return n_bins - 1
    return i


def _bin_center_price(bin_idx: int, p_min: float, bin_w: float) -> float:
    return p_min + (bin_idx + 0.5) * bin_w


def _level_bin_indices(
    price: float, p_min: float, bin_w: float, n_bins: int
) -> List[int]:
    """Bin zawierający poziom + po jednym sąsiednim z każdej strony (max 3)."""
    center = _price_to_bin_idx(price, p_min, bin_w, n_bins)
    lo = max(0, center - 1)
    hi = min(n_bins - 1, center + 1)
    return list(range(lo, hi + 1))


def _compute_value_area(
    bins: Sequence[float],
    p_min: float,
    bin_w: float,
    n_bins: int,
    poc_idx: int,
    total_vol: float,
) -> Tuple[Optional[float], Optional[float]]:
    """VA 70% — od POC rozszerzaj w stronę binu z większym wolumenem."""
    if total_vol <= 0.0 or bin_w <= 0.0:
        return None, None

    target = total_vol * VA_VOLUME_PCT
    cum = float(bins[poc_idx])
    low_i = high_i = poc_idx

    while cum < target:
        below = low_i - 1
        above = high_i + 1
        vol_below = float(bins[below]) if below >= 0 else -1.0
        vol_above = float(bins[above]) if above < n_bins else -1.0
        if vol_below < 0.0 and vol_above < 0.0:
            break
        if vol_above > vol_below:
            high_i = above
            cum += float(bins[above])
        else:
            low_i = below
            cum += float(bins[below])

    va_low = p_min + low_i * bin_w
    va_high = p_min + (high_i + 1) * bin_w
    return va_low, va_high


def _build_bins(
    candles: Sequence[Dict[str, Any]], n_bins: int
) -> Tuple[List[float], float, float, float]:
    """Zwraca (bins, p_min, p_max, bin_w)."""
    p_min = min(float(c["low"]) for c in candles)
    p_max = max(float(c["high"]) for c in candles)
    if p_max <= p_min:
        return [0.0] * n_bins, p_min, p_max, 0.0

    bin_w = (p_max - p_min) / n_bins
    bins = [0.0] * n_bins

    for c in candles:
        vol = float(c.get("volume", 0.0) or 0.0)
        if vol <= 0.0:
            continue
        lo_i = _price_to_bin_idx(float(c["low"]), p_min, bin_w, n_bins)
        hi_i = _price_to_bin_idx(float(c["high"]), p_min, bin_w, n_bins)
        covered = hi_i - lo_i + 1
        share = vol / covered
        for i in range(lo_i, hi_i + 1):
            bins[i] += share

    return bins, p_min, p_max, bin_w


def _avg_bin_vol(indices: Sequence[int], bins: Sequence[float]) -> Optional[float]:
    if not indices:
        return None
    return sum(float(bins[i]) for i in indices) / len(indices)


def _price_outside_va(price: float, va_low: Optional[float], va_high: Optional[float]) -> bool:
    if va_low is None or va_high is None:
        return False
    return price < va_low or price > va_high


def _zone_outside_va(
    zone_lo: float, zone_hi: float, va_low: Optional[float], va_high: Optional[float]
) -> bool:
    if va_low is None or va_high is None:
        return False
    lo, hi = (zone_lo, zone_hi) if zone_lo <= zone_hi else (zone_hi, zone_lo)
    return hi < va_low or lo > va_high


def compute_horizon_vp(
    candles: Sequence[Dict[str, Any]],
    *,
    ob_high: float,
    ob_low: float,
    entry: float,
    sl: float,
    required_candles: int,
    n_bins: int,
    prefix: str,
) -> Dict[str, Any]:
    """Profil VP dla jednego horyzontu — płaski dict z prefiksem."""
    out = _empty_horizon_fields(prefix, vp_candles_used=len(candles))
    n = len(candles)
    if n < VP_MIN_CANDLES:
        return out

    try:
        hi = float(ob_high)
        lo = float(ob_low)
        entry_p = float(entry)
        sl_p = float(sl)
    except (TypeError, ValueError):
        return out
    if hi < lo:
        hi, lo = lo, hi

    bins, p_min, p_max, bin_w = _build_bins(candles, n_bins)
    out[f"{prefix}vp_price_min"] = p_min
    out[f"{prefix}vp_price_max"] = p_max
    out[f"{prefix}vp_bin_width"] = bin_w if bin_w > 0.0 else None

    nonempty = [v for v in bins if v > 0.0]
    vp_median = statistics.median(nonempty) if nonempty else 0.0
    out[f"{prefix}vp_median_bin_vol"] = vp_median if vp_median > 0.0 else None

    total_vol = sum(bins)
    if total_vol <= 0.0 or bin_w <= 0.0:
        out[f"{prefix}vp_reliable"] = False
        return out

    poc_idx = max(range(n_bins), key=lambda i: bins[i])
    poc_price = _bin_center_price(poc_idx, p_min, bin_w)
    out[f"{prefix}poc_price"] = poc_price

    va_low, va_high = _compute_value_area(bins, p_min, bin_w, n_bins, poc_idx, total_vol)
    out[f"{prefix}va_low"] = va_low
    out[f"{prefix}va_high"] = va_high

    mid_ob = (hi + lo) / 2.0
    if poc_price > 0.0:
        out[f"{prefix}ob_dist_from_poc_pct"] = abs(mid_ob - poc_price) / poc_price * 100.0

    out[f"{prefix}ob_outside_va"] = _zone_outside_va(lo, hi, va_low, va_high)
    out[f"{prefix}sl_outside_va"] = _price_outside_va(sl_p, va_low, va_high)
    out[f"{prefix}entry_outside_va"] = _price_outside_va(entry_p, va_low, va_high)

    zlo_i = _price_to_bin_idx(lo, p_min, bin_w, n_bins)
    zhi_i = _price_to_bin_idx(hi, p_min, bin_w, n_bins)
    zone_bins_covered = zhi_i - zlo_i + 1
    out[f"{prefix}ob_zone_bins_covered"] = zone_bins_covered

    if zone_bins_covered > 0:
        zone_avg = sum(bins[i] for i in range(zlo_i, zhi_i + 1)) / zone_bins_covered
        out[f"{prefix}ob_zone_bin_vol_avg"] = zone_avg
        if vp_median > 0.0:
            out[f"{prefix}ob_vp_ratio"] = zone_avg / vp_median

    sl_indices = _level_bin_indices(sl_p, p_min, bin_w, n_bins)
    entry_indices = _level_bin_indices(entry_p, p_min, bin_w, n_bins)
    sl_avg = _avg_bin_vol(sl_indices, bins)
    entry_avg = _avg_bin_vol(entry_indices, bins)
    if sl_avg is not None:
        out[f"{prefix}sl_bin_vol_avg"] = sl_avg
        if vp_median > 0.0:
            out[f"{prefix}sl_vp_ratio"] = sl_avg / vp_median
    if entry_avg is not None:
        out[f"{prefix}entry_bin_vol_avg"] = entry_avg
        if vp_median > 0.0:
            out[f"{prefix}entry_vp_ratio"] = entry_avg / vp_median

    ratios_ok = (
        out.get(f"{prefix}ob_vp_ratio") is not None
        or out.get(f"{prefix}sl_vp_ratio") is not None
        or out.get(f"{prefix}entry_vp_ratio") is not None
    )
    out[f"{prefix}vp_reliable"] = (
        n >= required_candles
        and vp_median > 0.0
        and ratios_ok
    )
    return out


def build_dual_vp_context(
    candles: Sequence[Dict[str, Any]],
    *,
    ob_high: float,
    ob_low: float,
    entry: float,
    sl: float,
) -> Dict[str, Any]:
    """
    Dwa horyzonty z jednego zbioru świec (long = pełny fetch, short = suffix).
  candles powinny być posortowane rosnąco po ts, przycięte do VP_PERIOD_LONG.
    """
    if not candles:
        return empty_dual_vp_context()

    long_c = list(candles)[-VP_PERIOD_LONG:]
    short_c = list(candles)[-VP_PERIOD_SHORT:]

    out: Dict[str, Any] = {
        "vp_bins_short": VP_BINS_SHORT,
        "vp_bins_long": VP_BINS_LONG,
    }
    out.update(
        compute_horizon_vp(
            short_c,
            ob_high=ob_high,
            ob_low=ob_low,
            entry=entry,
            sl=sl,
            required_candles=VP_PERIOD_SHORT,
            n_bins=VP_BINS_SHORT,
            prefix=_HORIZON_PREFIXES["short"],
        )
    )
    out.update(
        compute_horizon_vp(
            long_c,
            ob_high=ob_high,
            ob_low=ob_low,
            entry=entry,
            sl=sl,
            required_candles=VP_PERIOD_LONG,
            n_bins=VP_BINS_LONG,
            prefix=_HORIZON_PREFIXES["long"],
        )
    )
    return out
