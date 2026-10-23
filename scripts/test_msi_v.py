#!/usr/bin/env python3
"""Test syntetyczny: MSI sekcja V (cykl HL/LH → kolejne OB → restart III/IV)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orderflow_engine.msi_engine import MsiCandle, MsiEngine, MsiPhase, VCyclePhase


def _c(ts: int, high: float, low: float) -> MsiCandle:
    return MsiCandle(ts=ts, open=low, high=high, low=low, close=high)


def _first_ob_up(eng: MsiEngine) -> None:
    # IC low=95, żeby późniejsze Temp HL (98→96) nie odpalały CHOCH (VI)
    eng.on_candle_close(_c(1, 110.0, 95.0))
    eng.on_candle_close(_c(2, 115.0, 105.0))
    eng.on_candle_close(_c(3, 120.0, 106.0))


def run() -> None:
    events: list[str] = []

    def sink(ev, _ob):
        events.append(ev.event_type)

    eng = MsiEngine("V_UP", on_event=sink)
    _first_ob_up(eng)
    assert len(eng.orderblocks) == 1
    assert eng.state.liquidity == 105.0
    print("OK  V setup: pierwszy OB LONG, liquidity=105")

    eng.on_candle_close(_c(10, 125.0, 108.0))
    # Grab bez IC_RESET: L<liq, H nie > IC_HIGH (110)
    eng.on_candle_close(_c(11, 108.0, 98.0))
    assert eng.state.v_cycle == VCyclePhase.UPDATING_TEMP_HL
    # Temp HL update bez IC_RESET: L < 98, H <= 110
    eng.on_candle_close(_c(12, 109.0, 96.0))
    assert eng.state.temporary_higher_low == 96.0
    assert eng.state.hl_marking_candle.ts == 12
    # BOS HIGH: H > 125, L >= IC_LOW (100) żeby nie było IC_RESET
    eng.on_candle_close(_c(13, 130.0, 101.0))
    assert len(eng.orderblocks) == 2
    ob2 = eng.orderblocks[1]
    assert ob2.candle.ts == 12
    assert ob2.hl_lh_level == 96.0
    # PDF V.6: po subsequent OB → III/IV, NIE WAIT_LIQ_GRAB na starej liq
    assert eng.state.liquidity is None
    assert eng.state.phase in (MsiPhase.INITIAL_STRUCTURE, MsiPhase.PRE_FIRST_OB)
    assert eng.state.initial_candle is not None
    assert eng.state.initial_candle.ts == 12
    print("OK  V UP: drugi OB + restart III/IV (liquidity=None)")

    # III/IV od IC=#12 (H=109,L=96); trigger #13 dał już PRE_FIRST_OB
    # temp_high=130, temp_liq=101 → BOS: H>130 AND L>101
    n_ob = len(eng.orderblocks)
    eng.on_candle_close(_c(14, 131.0, 102.0))
    assert eng.state.phase == MsiPhase.HL_LH_CYCLE
    assert eng.state.v_cycle == VCyclePhase.WAIT_LIQ_GRAB
    assert eng.state.liquidity is not None
    assert len(eng.orderblocks) == n_ob  # bez drugiego OB_NEW na tej samej IC
    print("OK  V UP: po IV — nowa LIQUIDITY, bez 2x OB_NEW")

    # V: grab nowej liq → HL → trzeci OB
    liq = eng.state.liquidity
    assert liq is not None
    eng.on_candle_close(_c(20, 128.0, liq - 1))
    assert eng.state.v_cycle == VCyclePhase.UPDATING_TEMP_HL
    bos_h = eng.state.bos_high
    assert bos_h is not None
    eng.on_candle_close(_c(21, bos_h + 1, liq - 2))
    assert len(eng.orderblocks) == n_ob + 1
    print("OK  V UP: trzeci OB po pelnym III/IV + V")

    events.clear()
    eng2 = MsiEngine("V_DN", on_event=lambda e, _o: events.append(e.event_type))
    eng2.on_candle_close(_c(100, 200.0, 190.0))
    eng2.on_candle_close(_c(101, 195.0, 185.0))
    eng2.on_candle_close(_c(102, 194.0, 184.0))
    eng2.on_candle_close(_c(110, 196.0, 183.0))
    eng2.on_candle_close(_c(111, 198.0, 186.0))
    eng2.on_candle_close(_c(112, 187.0, 183.0))
    assert len(eng2.orderblocks) == 2
    assert eng2.orderblocks[1].direction == "SHORT"
    assert eng2.state.liquidity is None  # restart III/IV
    assert eng2.state.phase in (MsiPhase.INITIAL_STRUCTURE, MsiPhase.PRE_FIRST_OB)
    print("OK  V DOWN: drugi OB SHORT + restart III/IV")

    print("\nWszystkie testy sekcji V przeszly.")


if __name__ == "__main__":
    run()
