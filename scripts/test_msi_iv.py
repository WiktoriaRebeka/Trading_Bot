#!/usr/bin/env python3
"""Test syntetyczny: MSI sekcja IV (BOS + LIQUIDITY + pierwszy OB)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orderflow_engine.msi_engine import MsiCandle, MsiEngine, MsiPhase


def _c(ts: int, high: float, low: float) -> MsiCandle:
    return MsiCandle(ts=ts, open=low, high=high, low=low, close=high)


def _boot_up_trend(eng: MsiEngine) -> None:
    eng.on_candle_close(_c(1, 110.0, 100.0))   # IC
    eng.on_candle_close(_c(2, 115.0, 105.0))   # INITIAL_TREND UP


def run() -> None:
    events: list[str] = []

    def sink(ev, _ob):
        events.append(ev.event_type)

    # --- UP: wiele świec budowy przed BOS ---
    eng = MsiEngine("UP", on_event=sink)
    _boot_up_trend(eng)
    assert eng.state.temporary_high == 115.0
    assert eng.state.temporary_liquidity == 105.0

    eng.on_candle_close(_c(3, 112.0, 106.0))   # ignoruj (low >= temp_liq, high < temp_high)
    temp_updates_after_c3 = events.count("TEMP_UPDATE")
    assert temp_updates_after_c3 == 0

    eng.on_candle_close(_c(4, 114.0, 102.0))   # LOW < temp_liq 105 → 102
    assert eng.state.temporary_liquidity == 102.0

    eng.on_candle_close(_c(5, 114.0, 104.0))   # high nie > 115 → ignoruj
    assert eng.state.temporary_high == 115.0

    eng.on_candle_close(_c(6, 117.0, 103.0))   # high>115, low>102 ale low<115... low=103>102, high=117>115 → BOS?
    # low=103 > temp_liq=102, high=117 > temp_high=115 → YES BOS on candle 6

    assert "OB_NEW" in events
    ob = eng.orderblocks[0]
    assert ob.direction == "LONG"
    assert ob.ob_low == 100.0 and ob.ob_high == 110.0  # Initial Candle
    assert ob.hl_lh_level == 100.0  # Initial LOW = HIGHER LOW
    assert ob.liquidity_level == 102.0  # promoted temp liq at BOS moment
    assert ob.bos_level == 115.0
    assert eng.state.phase == MsiPhase.HL_LH_CYCLE
    print("OK  IV UP: 6 swiec (trend + 4 building) -> pierwszy OB LONG na IC [100,110]")

    # --- UP: BOS na jednej świecy zaraz po trend (minimal path) ---
    events.clear()
    eng2 = MsiEngine("UP2", on_event=lambda ev, _ob: events.append(ev.event_type))
    _boot_up_trend(eng2)
    eng2.on_candle_close(_c(10, 120.0, 106.0))  # high>115, low>105
    assert events[-4:] == ["LIQUIDITY", "BOS", "HL_LH_CONFIRMED", "OB_NEW"]
    print("OK  IV UP: BOS jedna swieca po trend")

    # --- DOWN symetrycznie ---
    events.clear()
    eng3 = MsiEngine("DN", on_event=lambda ev, _ob: events.append(ev.event_type))
    eng3.on_candle_close(_c(20, 200.0, 190.0))   # IC
    eng3.on_candle_close(_c(21, 195.0, 185.0))   # DOWN trend: temp_low=185, temp_liq=195
    eng3.on_candle_close(_c(22, 194.0, 187.0))   # ignoruj
    eng3.on_candle_close(_c(23, 196.0, 186.0))   # high>195 → temp_liq=196
    eng3.on_candle_close(_c(24, 193.0, 184.0))   # low<185, high<196 → BOS
    ob3 = eng3.orderblocks[0]
    assert ob3.direction == "SHORT"
    assert ob3.ob_high == 200.0 and ob3.ob_low == 190.0
    assert ob3.hl_lh_level == 200.0
    print("OK  IV DOWN: building + BOS -> pierwszy OB SHORT")

    print("\nWszystkie testy sekcji IV przeszly.")


if __name__ == "__main__":
    run()
