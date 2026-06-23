#!/usr/bin/env python3
"""Test syntetyczny: MSI sekcje II–III (Initial Candle + Initial Trend)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orderflow_engine.msi_engine import MsiCandle, MsiEngine, MsiPhase, Trend


def _c(ts: int, high: float, low: float) -> MsiCandle:
    return MsiCandle(ts=ts, open=low, high=high, low=low, close=high)


def run() -> None:
    events_log: list[str] = []

    def sink(ev, _ob):
        events_log.append(ev.event_type)

    eng = MsiEngine("TEST", on_event=sink)

    # --- Sekcja II: pierwsza świeca = IC ---
    eng.on_candle_close(_c(1, 110.0, 100.0))
    assert eng.state.phase == MsiPhase.INITIAL_STRUCTURE
    assert eng.state.initial_high == 110.0
    assert eng.state.initial_low == 100.0
    assert events_log == ["IC_SET"]
    print("OK  II: pierwsza swieca = IC [100, 110]")

    # --- Sekcja III: ignoruj świecę wewnątrz IC ---
    eng.on_candle_close(_c(2, 108.0, 102.0))
    assert eng.state.initial_trend is None
    assert eng.state.phase == MsiPhase.INITIAL_STRUCTURE
    assert events_log == ["IC_SET"]
    print("OK  III: swieca wewnatrz IC -> ignoruj")

    # --- Sekcja III: UP trend ---
    eng.on_candle_close(_c(3, 115.0, 105.0))
    assert eng.state.initial_trend == Trend.UP
    assert eng.state.temporary_high == 115.0
    assert eng.state.temporary_liquidity == 105.0
    assert eng.state.phase == MsiPhase.PRE_FIRST_OB
    assert "INITIAL_TREND" in events_log
    print("OK  III: HIGH > IC_HIGH -> UP, temp_high=115, temp_liq=105")

    # --- Nowy silnik: DOWN trend ---
    events_log.clear()
    eng2 = MsiEngine("TEST2", on_event=lambda ev, _ob: events_log.append(ev.event_type))
    eng2.on_candle_close(_c(10, 200.0, 190.0))
    eng2.on_candle_close(_c(11, 195.0, 185.0))
    assert eng2.state.initial_trend == Trend.DOWN
    assert eng2.state.temporary_low == 185.0
    assert eng2.state.temporary_liquidity == 195.0
    print("OK  III: LOW < IC_LOW -> DOWN, temp_low=185, temp_liq=195")

    # --- Sekcja II: reset IC (oba poza zakresem) ---
    events_log.clear()
    eng3 = MsiEngine("TEST3", on_event=lambda ev, _ob: events_log.append(ev.event_type))
    eng3.on_candle_close(_c(20, 100.0, 90.0))
    eng3.on_candle_close(_c(21, 105.0, 95.0))  # inside
    eng3.on_candle_close(_c(22, 120.0, 80.0))  # high>100 AND low<90
    assert events_log[-1] == "IC_RESET"
    assert eng3.state.initial_high == 120.0
    assert eng3.state.initial_low == 80.0
    assert eng3.state.phase == MsiPhase.INITIAL_STRUCTURE
    print("OK  II: reset IC gdy HIGH>IC_HIGH i LOW<IC_LOW -> nowa IC [80,120]")

    print("\nWszystkie testy sekcji II-III przeszly.")


if __name__ == "__main__":
    run()
