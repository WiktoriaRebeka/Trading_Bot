# orderflow_engine/ob_orderflow_snapshot.py
# Cechy orderflow do logu OB (nie blokują wejścia).

from __future__ import annotations

from typing import Any, Dict, Optional


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
