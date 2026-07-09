# orderflow_engine/ob_orderflow_snapshot.py
# Cechy orderflow do logu OB (nie blokują wejścia).

from __future__ import annotations

import os
import statistics
from typing import Any, Dict, Optional

# --- Volume Profile wokół strefy OB (tylko log, bez gate'u wejścia) ---
VP_LOOKBACK_CANDLES = int(os.environ.get("VP_LOOKBACK_CANDLES", "120"))
VP_BINS = int(os.environ.get("VP_BINS", "50"))
VP_MIN_CANDLES = int(os.environ.get("VP_MIN_CANDLES", "30"))
# Bufor świec musi pomieścić okno + luz na opóźnienie detekcji (marking→trigger).
VP_HISTORY_MARGIN = int(os.environ.get("VP_HISTORY_MARGIN", "160"))
VP_HISTORY_MAXLEN = VP_LOOKBACK_CANDLES + VP_HISTORY_MARGIN


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


def _empty_vp(vp_window_candles: int = 0) -> Dict[str, Any]:
    """Szkielet cech VP — wszystkie pola zawsze obecne w raw_context (surowe, bez klasyfikacji)."""
    return {
        "ob_vp_ratio": None,
        "vp_lookback": VP_LOOKBACK_CANDLES,
        "ob_zone_bin_vol_avg": None,
        "vp_median_bin_vol": None,
        "vp_bins": VP_BINS,
        "vp_window_candles": vp_window_candles,
        "ob_zone_bins_covered": 0,
        "ob_zone_in_window": False,
    }


def compute_ob_vp_features(
    processor: Any,
    symbol: str,
    ob_high: Optional[float],
    ob_low: Optional[float],
    ob_candle_ts: Optional[int],
) -> Dict[str, Any]:
    """
    Profil wolumenu z okna VP_LOOKBACK_CANDLES świec 1M PRZED świecą OB.

    Metoda:
    - zakres cenowy okna [min(low), max(high)], podział na VP_BINS równych binów;
    - wolumen każdej świecy rozsmarowany RÓWNOMIERNIE po binach pokrytych jej [low, high];
    - ob_vp_ratio = (średni wolumen binów pokrytych strefą [OB_low, OB_high])
                    / (mediana wolumenu wszystkich niepustych binów okna).

    Zwraca WYŁĄCZNIE surowe liczby — bez progu, bez klasyfikacji LVN/HVN, bez gate'u.
    ob_vp_ratio = None gdy: za mało świec, zdegenerowany zakres, mediana=0,
    lub strefa OB nie jest w pełni zawarta w zmierzonym oknie (ob_zone_in_window=False).
    Nie kodujemy tezy „świeży teren = LVN" jako liczby — te przypadki filtruje się w SQL
    po ob_zone_in_window / ob_zone_bins_covered.
    """
    if processor is None or ob_high is None or ob_low is None or ob_candle_ts is None:
        return _empty_vp()

    sym = str(symbol).upper().replace(".P", "")
    history = getattr(processor, "candle_vol_history", None)
    if not history or sym not in history:
        return _empty_vp()

    try:
        hi = float(ob_high)
        lo = float(ob_low)
        ob_ts = int(ob_candle_ts)
    except (TypeError, ValueError):
        return _empty_vp()
    if hi < lo:
        hi, lo = lo, hi

    window = [c for c in history[sym] if int(c["ts"]) < ob_ts][-VP_LOOKBACK_CANDLES:]
    n = len(window)
    if n < VP_MIN_CANDLES:
        return _empty_vp(n)

    p_min = min(float(c["low"]) for c in window)
    p_max = max(float(c["high"]) for c in window)
    if p_max <= p_min:
        return _empty_vp(n)

    bin_w = (p_max - p_min) / VP_BINS
    bins = [0.0] * VP_BINS

    def _idx(price: float) -> int:
        i = int((price - p_min) / bin_w)
        if i < 0:
            return 0
        if i > VP_BINS - 1:
            return VP_BINS - 1
        return i

    for c in window:
        vol = float(c.get("volume", 0.0) or 0.0)
        if vol <= 0.0:
            continue
        lo_i = _idx(float(c["low"]))
        hi_i = _idx(float(c["high"]))
        covered = hi_i - lo_i + 1
        share = vol / covered
        for i in range(lo_i, hi_i + 1):
            bins[i] += share

    nonempty = [v for v in bins if v > 0.0]
    vp_median_bin_vol = statistics.median(nonempty) if nonempty else 0.0

    # Strefa OB w pełni zawarta w zmierzonym zakresie → cecha jest w całości „zmierzona".
    zone_in_window = (lo >= p_min) and (hi <= p_max)
    # Liczba binów pokrytych przez (zaklamrowaną) strefę — audyt także dla przypadków częściowych.
    overlap = (hi >= p_min) and (lo <= p_max)
    zlo_i = _idx(max(lo, p_min)) if overlap else 0
    zhi_i = _idx(min(hi, p_max)) if overlap else -1
    zone_bins_covered = (zhi_i - zlo_i + 1) if overlap else 0

    out = _empty_vp(n)
    out["vp_median_bin_vol"] = vp_median_bin_vol
    out["ob_zone_bins_covered"] = zone_bins_covered
    out["ob_zone_in_window"] = zone_in_window

    if not zone_in_window or zone_bins_covered <= 0:
        # Strefa OB (częściowo lub całkowicie) poza oknem — NIE mieszamy „zmierzonej pustki"
        # z „niezmierzonym": ratio i avg = None, ale surowe liczniki zostają.
        return out

    zone_avg = sum(bins[i] for i in range(zlo_i, zhi_i + 1)) / zone_bins_covered
    out["ob_zone_bin_vol_avg"] = zone_avg
    out["ob_vp_ratio"] = (zone_avg / vp_median_bin_vol) if vp_median_bin_vol > 0.0 else None
    return out
