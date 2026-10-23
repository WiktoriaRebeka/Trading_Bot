#!/usr/bin/env python3
"""Test: zmiana trendu + budowa III/IV + kolejne OB w trendzie."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orderflow_engine.msi_engine import (
    ChochMode,
    MsiCandle,
    MsiEngine,
    MsiPhase,
    OrderBlock,
    Trend,
    VCyclePhase,
    _MsiState,
)


def _c(ts: int, high: float, low: float) -> MsiCandle:
    return MsiCandle(ts=ts, open=low, high=high, low=low, close=high)


def _first_ob_long(eng: MsiEngine) -> None:
    eng.on_candle_close(_c(1, 110.0, 100.0))
    eng.on_candle_close(_c(2, 115.0, 105.0))
    eng.on_candle_close(_c(3, 120.0, 106.0))
    assert eng.state.current_ob is not None
    assert eng.state.current_ob.direction == "LONG"


def run() -> None:
    evs: list = []

    # --- UP -> DOWN: po zmianie NIE WAIT_LIQ_GRAB z liq=OB; start III/IV ---
    eng = MsiEngine("TC_DOWN", on_event=lambda e, _o: evs.append(e))
    _first_ob_long(eng)
    ob_low = eng.state.current_ob.ob_low
    eng.on_candle_close(_c(4, 125.0, 108.0))
    eng.on_candle_close(_c(5, 110.0, ob_low - 1))
    assert eng.state.initial_trend == Trend.DOWN
    assert eng.orderblocks[-1].direction == "SHORT"
    assert eng.orderblocks[-1].ob_high == 125.0
    # Trigger low 99 < extreme.low 108 → od razu PRE_FIRST_OB
    assert eng.state.phase == MsiPhase.PRE_FIRST_OB
    assert eng.state.liquidity is None
    assert eng.state.v_cycle != VCyclePhase.WAIT_LIQ_GRAB or eng.state.liquidity is None
    assert any(e.event_type == "CHOCH" and e.payload.get("mode") == "TREND_CHANGE" for e in evs)
    print("OK  trend-change UP->DOWN: PRE_FIRST_OB, liquidity=None")

    # Dokończ IV DOWN: BOS bez drugiego OB_NEW na tej samej IC
    n_ob = len(eng.orderblocks)
    eng.on_candle_close(_c(6, 105.0, 97.0))  # może update temps
    # BOS: low < temp_low AND high < temp_liq
    # po #5: temp_low=99, temp_liq=110; #6 low=97<99, high=105<110 → BOS
    assert eng.state.phase == MsiPhase.HL_LH_CYCLE
    assert eng.state.v_cycle == VCyclePhase.WAIT_LIQ_GRAB
    assert eng.state.liquidity is not None
    assert len(eng.orderblocks) == n_ob  # bez drugiego OB_NEW
    print("OK  po IV BOS: HL_LH_CYCLE WAIT_LIQ_GRAB, bez 2x OB_NEW")

    # --- Kolejne OB w trendzie DOWN: grab + BOS → subsequent OB → III/IV restart ---
    liq = eng.state.liquidity
    assert liq is not None
    eng.on_candle_close(_c(7, liq + 1, 96.0))  # grab high > liq
    assert eng.state.v_cycle == VCyclePhase.UPDATING_TEMP_HL
    bos_l = eng.state.bos_low
    assert bos_l is not None
    eng.on_candle_close(_c(8, 104.0, bos_l - 1))  # break BOS low → nowy SHORT OB
    assert len(eng.orderblocks) == n_ob + 1
    assert eng.orderblocks[-1].direction == "SHORT"
    # Po subsequent OB: budowa III/IV, nie WAIT_LIQ_GRAB na starej liq
    assert eng.state.liquidity is None
    assert eng.state.phase in (MsiPhase.INITIAL_STRUCTURE, MsiPhase.PRE_FIRST_OB)
    print("OK  kolejne OB SHORT + restart III/IV (PDF V.6)")

    # --- Migracja CHOCH_PENDING ---
    choch = _c(1000, 108.0, 95.0)
    ob = OrderBlock(
        chain_id="LEGACY-1",
        candle=_c(900, 110.0, 96.0),
        direction="LONG",
        hl_lh_level=96.0,
        bos_level=110.0,
        liquidity_level=94.0,
        initial_trend="UP",
        detected_at_ts=900,
        is_first=False,
    )
    raw = {
        "phase": "CHOCH_PENDING",
        "initial_trend": "UP",
        "choch_candle": choch.to_dict(),
        "choch_mode": "PENDING",
        "choch_low_intact": False,
        "bos_high": 108.0,
        "current_ob": ob.to_dict(),
        "liquidity": 94.0,
        "v_cycle": "UPDATING_TEMP_HL",
        "pre_bos_phase": "BUILDING",
        "chain_seq": 1,
    }
    eng3 = MsiEngine("LEGACY")
    eng3._state = _MsiState.from_dict(raw)
    eng3.on_candle_close(_c(2000, 109.0, 97.0))
    assert eng3.state.phase == MsiPhase.HL_LH_CYCLE
    assert eng3.state.choch_mode == ChochMode.NONE
    eng3.on_candle_close(_c(2060, 108.0, 95.0))
    assert eng3.state.initial_trend == Trend.DOWN
    assert eng3.orderblocks[-1].direction == "SHORT"
    print("OK  migracja CHOCH_PENDING + trend-change")

    # --- Migracja legacy WAIT_LIQ_GRAB po subsequent OB (Firestore schema < 2) ---
    marking = _c(500, 112.0, 100.0)
    ob_sub = OrderBlock(
        chain_id="SUB-1",
        candle=marking,
        direction="LONG",
        hl_lh_level=100.0,
        bos_level=112.0,
        liquidity_level=94.0,  # stara Liquidity z poprzedniego cyklu
        initial_trend="UP",
        detected_at_ts=500,
        is_first=False,
    )
    raw_wait = {
        "phase": "HL_LH_CYCLE",
        "initial_trend": "UP",
        "initial_candle": marking.to_dict(),
        "initial_high": marking.high,
        "initial_low": marking.low,
        "current_ob": ob_sub.to_dict(),
        "liquidity": 94.0,
        "temporary_high": 120.0,
        "temporary_liquidity": marking.low,
        "bos_high": 120.0,
        "v_cycle": "WAIT_LIQ_GRAB",
        "pre_bos_phase": "BUILDING",
        "chain_seq": 2,
    }
    eng4 = MsiEngine("MIG_WAIT")
    eng4.import_state(
        {
            "state": raw_wait,
            "candle_count": 10,
            "schema_version": 1,  # legacy → migracja
        }
    )
    assert eng4.state.phase == MsiPhase.INITIAL_STRUCTURE
    assert eng4.state.liquidity is None
    assert eng4.state.initial_candle is not None
    assert eng4.state.initial_candle.ts == marking.ts
    assert eng4.state.current_ob is not None
    assert eng4.state.current_ob.chain_id == "SUB-1"
    # Po migracji: Breaking Candle w kierunku UP → PRE_FIRST_OB
    eng4.on_candle_close(_c(600, 113.0, 101.0))
    assert eng4.state.phase == MsiPhase.PRE_FIRST_OB
    assert eng4.state.temporary_high == 113.0
    print("OK  migracja WAIT_LIQ_GRAB subsequent -> INITIAL_STRUCTURE + III")

    # schema_version>=2: nie migruj ponownie poprawnego WAIT_LIQ_GRAB po IV
    eng5 = MsiEngine("NO_MIG")
    eng5.import_state(
        {
            "state": {
                **raw_wait,
                "liquidity": 101.0,  # świeżo z IV
            },
            "candle_count": 10,
            "schema_version": 2,
        }
    )
    assert eng5.state.phase == MsiPhase.HL_LH_CYCLE
    assert eng5.state.v_cycle == VCyclePhase.WAIT_LIQ_GRAB
    assert eng5.state.liquidity == 101.0
    print("OK  schema_version=2: brak ponownej migracji WAIT_LIQ_GRAB")

    print("\nWszystkie testy zmiany trendu przeszly.")


if __name__ == "__main__":
    run()
