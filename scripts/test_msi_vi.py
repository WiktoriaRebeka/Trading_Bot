#!/usr/bin/env python3
"""Test syntetyczny: MSI sekcja VI (CHOCH opcja A i B)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orderflow_engine.msi_engine import MsiCandle, MsiEngine, MsiPhase, VCyclePhase


def _c(ts: int, high: float, low: float) -> MsiCandle:
    return MsiCandle(ts=ts, open=low, high=high, low=low, close=high)


def _setup_second_ob_long(eng: MsiEngine) -> None:
    """Dwa OB LONG: drugi ma hl_lh=96, liquidity po grabie=98 (hl > liq dla czystego CHOCH)."""
    eng.on_candle_close(_c(1, 110.0, 100.0))
    eng.on_candle_close(_c(2, 115.0, 105.0))
    eng.on_candle_close(_c(3, 120.0, 106.0))
    eng.on_candle_close(_c(10, 125.0, 108.0))
    eng.on_candle_close(_c(11, 108.0, 98.0))
    eng.on_candle_close(_c(12, 109.0, 96.0))
    eng.on_candle_close(_c(13, 130.0, 101.0))


def run() -> None:
    evs: list[str] = []

    # --- Opcja A: CHOCH bez odwrócenia ---
    eng = MsiEngine("CHOCH_A", on_event=lambda e, _o: evs.append(e.event_type))
    _setup_second_ob_long(eng)
    ob = eng.orderblocks[-1]
    assert ob.hl_lh_level == 96.0
    assert eng.state.liquidity == 98.0

    # CHOCH: 95 < hl_lh (96), ale 95 >= liquidity (98)? NIE - 95 < 98 to grab.
    # Użyj liq=94 po wcześniejszym grabie: najpierw obniż liquidity
    eng.on_candle_close(_c(20, 128.0, 97.0))  # grab, liq=97
    assert eng.state.liquidity == 97.0
    # Anuluj cykl V przez IC-safe świecę w WAIT... actually we're in UPDATING after grab
    # Uprość: drugi setup z ręcznie niższą liquidity
    eng2 = MsiEngine("CHOCH_A2", on_event=lambda e, _o: evs.append(e.event_type))
    _setup_second_ob_long(eng2)
    eng2.state.liquidity = 94.0  # symulacja po wcześniejszym grabie
    eng2.state.v_cycle = VCyclePhase.WAIT_LIQ_GRAB

    eng2.on_candle_close(_c(30, 108.0, 95.0))  # 95<96 CHOCH, 95>=94 brak grabu
    assert eng2.state.phase == MsiPhase.CHOCH_PENDING
    bos_h = eng2.state.bos_high
    eng2.on_candle_close(_c(31, bos_h + 1, 95.5))  # NO_REVERSAL
    assert eng2.state.phase == MsiPhase.HL_LH_CYCLE
    assert eng2.state.temporary_higher_low == 95.0
    print("OK  VI opcja A: CHOCH -> NO_REVERSAL")

    # --- Opcja B: odwrócenie UP → DOWN ---
    evs.clear()
    eng3 = MsiEngine("CHOCH_B", on_event=lambda e, _o: evs.append(e.event_type))
    _setup_second_ob_long(eng3)
    eng3.state.liquidity = 94.0
    eng3.state.v_cycle = VCyclePhase.WAIT_LIQ_GRAB
    eng3.on_candle_close(_c(40, 108.0, 95.0))   # CHOCH PENDING, bos_high=108
    eng3.on_candle_close(_c(41, 107.0, 93.0))  # low < CHOCH low → reversal path
    eng3.on_candle_close(_c(42, 111.0, 96.0))  # H>bos_high, L>=IC_LOW — REVERSAL bez IC_RESET
    assert len(eng3.orderblocks) == 3
    assert eng3.orderblocks[-1].direction == "SHORT"
    assert eng3.state.initial_trend.value == "DOWN"
    print("OK  VI opcja B: CHOCH -> REVERSAL -> OB SHORT")

    # --- IC_RESET jedyny reset ---
    evs.clear()
    eng4 = MsiEngine("IC_RST", on_event=lambda e, _o: evs.append(e.event_type))
    _setup_second_ob_long(eng4)
    eng4.on_candle_close(_c(50, 120.0, 95.0))  # H>IC_HIGH, L<IC_LOW (IC jeszcze [100,110] z ts12? )
    # Po drugim OB IC = marking candle [96, 109] from ts12
    ic = eng4.state.initial_candle
    assert ic is not None
    eng4.on_candle_close(_c(51, ic.high + 5, ic.low - 5))
    assert "IC_RESET" in evs
    print("OK  IC_RESET: jedyny pełny reset PDF II")

    print("\nWszystkie testy sekcji VI przeszly.")


if __name__ == "__main__":
    run()
