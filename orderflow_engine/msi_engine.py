# orderflow_engine/msi_engine.py
# Market Structure Indicator 2.0 — silnik struktury na świecach 1M (PDF sekcje II–VI).

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class MsiPhase(str, Enum):
    START = "START"
    INITIAL_STRUCTURE = "INITIAL_STRUCTURE"
    PRE_FIRST_OB = "PRE_FIRST_OB"
    HL_LH_CYCLE = "HL_LH_CYCLE"
    CHOCH_PENDING = "CHOCH_PENDING"


class ChochMode(str, Enum):
    NONE = "NONE"
    PENDING = "PENDING"
    NO_REVERSAL = "NO_REVERSAL"
    REVERSAL = "REVERSAL"


class Trend(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class VCyclePhase(str, Enum):
    WAIT_LIQ_GRAB = "WAIT_LIQ_GRAB"
    UPDATING_TEMP_HL = "UPDATING_TEMP_HL"


class PreBosPhase(str, Enum):
    """Sekcja IV: podfaza między Initial Trend a pierwszym OB."""
    BUILDING = "BUILDING"  # aktualizacja Temporary HIGH/LOW + Temporary Liquidity


@dataclass
class MsiCandle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> MsiCandle:
        return cls(
            ts=int(d["ts"]),
            open=float(d["open"]),
            high=float(d["high"]),
            low=float(d["low"]),
            close=float(d["close"]),
            volume=float(d.get("volume", 0.0)),
        )

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class OrderBlock:
    chain_id: str
    candle: MsiCandle
    direction: str  # LONG | SHORT
    hl_lh_level: float
    bos_level: float
    liquidity_level: float
    initial_trend: str
    detected_at_ts: int
    is_first: bool = False

    @property
    def ob_high(self) -> float:
        return self.candle.high

    @property
    def ob_low(self) -> float:
        return self.candle.low

    @property
    def ob_height(self) -> float:
        return self.candle.high - self.candle.low

    def to_dict(self) -> dict:
        return {
            "chain_id": self.chain_id,
            "candle": self.candle.to_dict(),
            "direction": self.direction,
            "hl_lh_level": self.hl_lh_level,
            "bos_level": self.bos_level,
            "liquidity_level": self.liquidity_level,
            "initial_trend": self.initial_trend,
            "detected_at_ts": self.detected_at_ts,
            "is_first": self.is_first,
        }

    @classmethod
    def from_dict(cls, d: dict) -> OrderBlock:
        return cls(
            chain_id=d["chain_id"],
            candle=MsiCandle.from_dict(d["candle"]),
            direction=d["direction"],
            hl_lh_level=float(d["hl_lh_level"]),
            bos_level=float(d["bos_level"]),
            liquidity_level=float(d["liquidity_level"]),
            initial_trend=d["initial_trend"],
            detected_at_ts=int(d["detected_at_ts"]),
            is_first=bool(d.get("is_first", False)),
        )


@dataclass
class StructureEvent:
    event_type: str
    symbol: str
    ts: int
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _MsiState:
    phase: MsiPhase = MsiPhase.START
    initial_candle: Optional[MsiCandle] = None
    initial_high: Optional[float] = None
    initial_low: Optional[float] = None
    initial_trend: Optional[Trend] = None

    temporary_high: Optional[float] = None
    temporary_low: Optional[float] = None
    temporary_liquidity: Optional[float] = None

    liquidity: Optional[float] = None
    bos_high: Optional[float] = None
    bos_low: Optional[float] = None

    temporary_higher_low: Optional[float] = None
    temporary_lower_high: Optional[float] = None
    confirmed_higher_low: Optional[float] = None
    confirmed_lower_high: Optional[float] = None

    hl_marking_candle: Optional[MsiCandle] = None
    lh_marking_candle: Optional[MsiCandle] = None

    previous_ob: Optional[OrderBlock] = None
    current_ob: Optional[OrderBlock] = None
    chain_seq: int = 0

    choch_candle: Optional[MsiCandle] = None
    choch_mode: ChochMode = ChochMode.NONE
    choch_low_intact: bool = True   # VI opcja A: wszystkie LOW >= CHOCH LOW (UP)
    choch_high_intact: bool = True  # VI opcja A: wszystkie HIGH <= CHOCH HIGH (DOWN)
    v_cycle: VCyclePhase = VCyclePhase.WAIT_LIQ_GRAB
    pre_bos_phase: PreBosPhase = PreBosPhase.BUILDING
    cycle_swept_liquidity: Optional[float] = None  # V: poziom zgrabowany w bieżącym cyklu

    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "initial_candle": self.initial_candle.to_dict() if self.initial_candle else None,
            "initial_high": self.initial_high,
            "initial_low": self.initial_low,
            "initial_trend": self.initial_trend.value if self.initial_trend else None,
            "temporary_high": self.temporary_high,
            "temporary_low": self.temporary_low,
            "temporary_liquidity": self.temporary_liquidity,
            "liquidity": self.liquidity,
            "bos_high": self.bos_high,
            "bos_low": self.bos_low,
            "temporary_higher_low": self.temporary_higher_low,
            "temporary_lower_high": self.temporary_lower_high,
            "confirmed_higher_low": self.confirmed_higher_low,
            "confirmed_lower_high": self.confirmed_lower_high,
            "hl_marking_candle": self.hl_marking_candle.to_dict() if self.hl_marking_candle else None,
            "lh_marking_candle": self.lh_marking_candle.to_dict() if self.lh_marking_candle else None,
            "previous_ob": self.previous_ob.to_dict() if self.previous_ob else None,
            "current_ob": self.current_ob.to_dict() if self.current_ob else None,
            "chain_seq": self.chain_seq,
            "choch_candle": self.choch_candle.to_dict() if self.choch_candle else None,
            "choch_mode": self.choch_mode.value,
            "choch_low_intact": self.choch_low_intact,
            "choch_high_intact": self.choch_high_intact,
            "v_cycle": self.v_cycle.value,
            "pre_bos_phase": self.pre_bos_phase.value,
            "cycle_swept_liquidity": self.cycle_swept_liquidity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> _MsiState:
        def _candle(raw: Optional[dict]) -> Optional[MsiCandle]:
            return MsiCandle.from_dict(raw) if raw is not None else None

        def _ob(raw: Optional[dict]) -> Optional[OrderBlock]:
            return OrderBlock.from_dict(raw) if raw is not None else None

        def _trend(raw: Optional[str]) -> Optional[Trend]:
            return Trend(raw) if raw is not None else None

        return cls(
            phase=MsiPhase(d.get("phase", MsiPhase.START.value)),
            initial_candle=_candle(d.get("initial_candle")),
            initial_high=d.get("initial_high"),
            initial_low=d.get("initial_low"),
            initial_trend=_trend(d.get("initial_trend")),
            temporary_high=d.get("temporary_high"),
            temporary_low=d.get("temporary_low"),
            temporary_liquidity=d.get("temporary_liquidity"),
            liquidity=d.get("liquidity"),
            bos_high=d.get("bos_high"),
            bos_low=d.get("bos_low"),
            temporary_higher_low=d.get("temporary_higher_low"),
            temporary_lower_high=d.get("temporary_lower_high"),
            confirmed_higher_low=d.get("confirmed_higher_low"),
            confirmed_lower_high=d.get("confirmed_lower_high"),
            hl_marking_candle=_candle(d.get("hl_marking_candle")),
            lh_marking_candle=_candle(d.get("lh_marking_candle")),
            previous_ob=_ob(d.get("previous_ob")),
            current_ob=_ob(d.get("current_ob")),
            chain_seq=int(d.get("chain_seq", 0)),
            choch_candle=_candle(d.get("choch_candle")),
            choch_mode=ChochMode(d.get("choch_mode", ChochMode.NONE.value)),
            choch_low_intact=bool(d.get("choch_low_intact", True)),
            choch_high_intact=bool(d.get("choch_high_intact", True)),
            v_cycle=VCyclePhase(d.get("v_cycle", VCyclePhase.WAIT_LIQ_GRAB.value)),
            pre_bos_phase=PreBosPhase(d.get("pre_bos_phase", PreBosPhase.BUILDING.value)),
            cycle_swept_liquidity=d.get("cycle_swept_liquidity"),
        )


EventSink = Callable[[StructureEvent, Optional[OrderBlock]], None]


class MsiEngine:
    """Silnik MSI 2.0 na zamkniętych świecach 1M. Używa wyłącznie high/low świecy."""

    def __init__(self, symbol: str, on_event: Optional[EventSink] = None):
        self.symbol = str(symbol).upper()
        self._on_event = on_event
        self._state = _MsiState()
        self._all_obs: List[OrderBlock] = []
        self._candle_count = 0
        self._last_processed_ts: Optional[int] = None

    @property
    def state(self) -> _MsiState:
        return self._state

    @property
    def orderblocks(self) -> List[OrderBlock]:
        return list(self._all_obs)

    def get_state_snapshot(self) -> Dict[str, Any]:
        s = self._state
        return {
            "symbol": self.symbol,
            "phase": s.phase.value,
            "candle_count": self._candle_count,
            "initial_trend": s.initial_trend.value if s.initial_trend else None,
            "initial_high": s.initial_high,
            "initial_low": s.initial_low,
            "temporary_high": s.temporary_high,
            "temporary_low": s.temporary_low,
            "temporary_liquidity": s.temporary_liquidity,
            "liquidity": s.liquidity,
            "bos_high": s.bos_high,
            "bos_low": s.bos_low,
            "confirmed_higher_low": s.confirmed_higher_low,
            "confirmed_lower_high": s.confirmed_lower_high,
            "choch_mode": s.choch_mode.value,
            "v_cycle": s.v_cycle.value,
            "pre_bos_phase": s.pre_bos_phase.value,
            "current_ob": self._ob_to_dict(s.current_ob),
            "previous_ob": self._ob_to_dict(s.previous_ob),
            "ob_count": len(self._all_obs),
        }

    def export_state(self) -> dict:
        return {
            "symbol": self.symbol,
            "candle_count": self._candle_count,
            "last_processed_ts": self._last_processed_ts,
            "all_obs_count": len(self._all_obs),
            "state": self._state.to_dict(),
            "schema_version": 1,
        }

    def import_state(self, d: dict) -> None:
        self._state = _MsiState.from_dict(d["state"])
        self._candle_count = int(d.get("candle_count", 0))
        self._last_processed_ts = d.get("last_processed_ts")

    def replay_candles(self, candles: List[MsiCandle]) -> List[OrderBlock]:
        for c in candles:
            self.on_candle_close(c)
        return self.orderblocks

    def on_candle_close(self, candle: MsiCandle) -> List[StructureEvent]:
        self._candle_count += 1
        self._last_processed_ts = candle.ts
        events: List[StructureEvent] = []

        if self._state.initial_candle is not None and self._state.phase != MsiPhase.START:
            # PDF II (jedyny dozwolony reset): obie strony IC przebite naraz
            if self._ic_reset(candle):
                ev = self._emit("IC_RESET", candle, reason="high_and_low_outside_ic")
                events.append(ev)
                self._apply_ic_reset(candle)
                self._dispatch(events, None)
                return events

        if self._state.phase == MsiPhase.START:
            self._init_first_ic(candle)
            events.append(self._emit("IC_SET", candle))
            self._dispatch(events, None)
            return events

        if self._state.phase == MsiPhase.INITIAL_STRUCTURE:
            ev = self._try_initial_trend(candle)
            if ev:
                events.append(ev)
            self._dispatch(events, None)
            return events

        if self._state.phase == MsiPhase.PRE_FIRST_OB:
            events.extend(self._process_pre_first_ob(candle))
            self._dispatch(events, None)
            return events

        if self._state.phase == MsiPhase.HL_LH_CYCLE:
            events.extend(self._process_hl_lh_cycle(candle))
            grab_this_bar = any(e.event_type == "LIQUIDITY_GRAB" for e in events)
            if (
                not grab_this_bar
                and self._state.v_cycle != VCyclePhase.UPDATING_TEMP_HL
                and self._state.choch_mode == ChochMode.NONE
                and self._state.current_ob
            ):
                choch = self._detect_choch(candle)
                if choch:
                    events.append(choch)
            self._dispatch(events, None)
            return events

        if self._state.phase == MsiPhase.CHOCH_PENDING:
            events.extend(self._process_choch(candle))
            self._dispatch(events, None)
            return events

        self._dispatch(events, None)
        return events

    # ------------------------------------------------------------------ #
    # Sekcja II — Initial Candle
    # ------------------------------------------------------------------ #

    def _init_first_ic(self, candle: MsiCandle) -> None:
        self._state.initial_candle = candle
        self._state.initial_high = candle.high
        self._state.initial_low = candle.low
        self._state.phase = MsiPhase.INITIAL_STRUCTURE

    def _ic_reset(self, candle: MsiCandle) -> bool:
        """PDF II: HIGH i LOW zamkniętej świecy poza IC HIGH i IC LOW."""
        assert self._state.initial_high is not None
        assert self._state.initial_low is not None
        return candle.high > self._state.initial_high and candle.low < self._state.initial_low

    def _apply_ic_reset(self, candle: MsiCandle) -> None:
        """
        PDF II — jedyny pełny reset silnika.
        Poprzedni łańcuch OB kończy się; zaczynamy od nowej Initial Candle.
        """
        prev_chain = self._state.current_ob.chain_id if self._state.current_ob else None
        self._state = _MsiState()
        self._init_first_ic(candle)
        if prev_chain:
            logger.info("[%s] IC_RESET: poprzedni chain=%s przerwany", self.symbol, prev_chain)

    def _set_initial_candle(self, candle: MsiCandle) -> None:
        """PDF V Step 6 / VI-B: nowa Initial Candle — aktualizacja IC, NIE reset łańcucha."""
        self._state.initial_candle = candle
        self._state.initial_high = candle.high
        self._state.initial_low = candle.low

    # ------------------------------------------------------------------ #
    # Sekcja III — Initial Trend
    # ------------------------------------------------------------------ #

    def _try_initial_trend(self, candle: MsiCandle) -> Optional[StructureEvent]:
        """
        PDF III: UP gdy HIGH > Initial HIGH; DOWN gdy LOW < Initial LOW.
        Breaking Candle ustawia Temporary HIGH/LOW + Temporary Liquidity.
        Świece bez przełamania → ignoruj (return None).
        """
        assert self._state.initial_high is not None
        assert self._state.initial_low is not None

        if candle.high > self._state.initial_high:
            self._state.initial_trend = Trend.UP
            self._state.temporary_high = candle.high
            self._state.temporary_liquidity = candle.low
            self._state.pre_bos_phase = PreBosPhase.BUILDING
            self._state.phase = MsiPhase.PRE_FIRST_OB
            return self._emit(
                "INITIAL_TREND",
                candle,
                trend="UP",
                breaking_candle_ts=candle.ts,
                temporary_high=candle.high,
                temporary_liquidity=candle.low,
                initial_high=self._state.initial_high,
                initial_low=self._state.initial_low,
            )

        if candle.low < self._state.initial_low:
            self._state.initial_trend = Trend.DOWN
            self._state.temporary_low = candle.low
            self._state.temporary_liquidity = candle.high
            self._state.pre_bos_phase = PreBosPhase.BUILDING
            self._state.phase = MsiPhase.PRE_FIRST_OB
            return self._emit(
                "INITIAL_TREND",
                candle,
                trend="DOWN",
                breaking_candle_ts=candle.ts,
                temporary_low=candle.low,
                temporary_liquidity=candle.high,
                initial_high=self._state.initial_high,
                initial_low=self._state.initial_low,
            )

        return None

    # ------------------------------------------------------------------ #
    # Sekcja IV — Temporary levels, budowa Temporary Liquidity, pierwszy OB
    # PDF str. 8–9: między Initial Trend a BOS może być WIELE świec.
    # ------------------------------------------------------------------ #

    def _process_pre_first_ob(self, candle: MsiCandle) -> List[StructureEvent]:
        if self._state.initial_trend == Trend.UP:
            return self._pre_first_ob_up(candle)
        return self._pre_first_ob_down(candle)

    def _pre_first_ob_up(self, candle: MsiCandle) -> List[StructureEvent]:
        """
        PDF IV (UP):
        BOS gdy JEDNOCZEŚNIE: HIGH > Temporary HIGH AND LOW > Temporary Liquidity.
        Przed BOS: proces budowy Temporary Liquidity (str. 8) — LOW < Temp Liq
        aktualizuje poziom; HIGH nieistotne dopóki nie przełamie Temporary HIGH.
        """
        events: List[StructureEvent] = []
        th = self._state.temporary_high
        tl = self._state.temporary_liquidity
        assert th is not None and tl is not None

        if candle.high > th and candle.low > tl:
            return self._mark_first_ob_up(candle, th, tl)

        updated = self._update_temps_up_building(candle, th, tl)
        if updated:
            events.append(
                self._emit(
                    "TEMP_UPDATE",
                    candle,
                    trend="UP",
                    pre_bos_phase=self._state.pre_bos_phase.value,
                    **updated,
                )
            )
        return events

    def _mark_first_ob_up(self, candle: MsiCandle, th: float, tl: float) -> List[StructureEvent]:
        """PDF str. 8–9: promocja LIQUIDITY, BOS, HIGHER LOW, pierwszy OB = Initial Candle."""
        events: List[StructureEvent] = []
        promoted_liquidity = tl
        bos_level = th

        self._state.liquidity = promoted_liquidity
        self._state.bos_high = bos_level
        self._state.temporary_liquidity = candle.low
        self._state.confirmed_higher_low = self._state.initial_low

        events.append(
            self._emit("LIQUIDITY", candle, level=promoted_liquidity, promoted_from="temporary")
        )
        events.append(self._emit("BOS", candle, level=bos_level, trend="UP"))
        events.append(
            self._emit(
                "HL_LH_CONFIRMED",
                candle,
                level=float(self._state.confirmed_higher_low),
                kind="HIGHER_LOW",
            )
        )

        ob = self._create_ob(
            candle=self._state.initial_candle,  # type: ignore[arg-type]
            direction="LONG",
            hl_lh_level=float(self._state.confirmed_higher_low),
            bos_level=bos_level,
            liquidity_level=promoted_liquidity,
            is_first=True,
            trigger_candle=candle,
        )
        events.append(self._emit_ob_new(ob, candle))
        self._state.phase = MsiPhase.HL_LH_CYCLE
        self._state.pre_bos_phase = PreBosPhase.BUILDING
        self._state.v_cycle = VCyclePhase.WAIT_LIQ_GRAB
        self._state.temporary_high = candle.high
        return events

    def _pre_first_ob_down(self, candle: MsiCandle) -> List[StructureEvent]:
        """
        PDF IV (DOWN) — symetrycznie:
        BOS gdy JEDNOCZEŚNIE: LOW < Temporary LOW AND HIGH < Temporary Liquidity.
        Przed BOS: HIGH > Temp Liq aktualizuje poziom; LOW nieistotne dopóki
        nie przełamie Temporary LOW.
        """
        events: List[StructureEvent] = []
        tlow = self._state.temporary_low
        tliq = self._state.temporary_liquidity
        assert tlow is not None and tliq is not None

        if candle.low < tlow and candle.high < tliq:
            return self._mark_first_ob_down(candle, tlow, tliq)

        updated = self._update_temps_down_building(candle, tlow, tliq)
        if updated:
            events.append(
                self._emit(
                    "TEMP_UPDATE",
                    candle,
                    trend="DOWN",
                    pre_bos_phase=self._state.pre_bos_phase.value,
                    **updated,
                )
            )
        return events

    def _mark_first_ob_down(self, candle: MsiCandle, tlow: float, tliq: float) -> List[StructureEvent]:
        """PDF str. 8–9 DOWN: LIQUIDITY, BOS, LOWER HIGH, pierwszy OB = Initial Candle."""
        events: List[StructureEvent] = []
        promoted_liquidity = tliq
        bos_level = tlow

        self._state.liquidity = promoted_liquidity
        self._state.bos_low = bos_level
        self._state.temporary_liquidity = candle.high
        self._state.confirmed_lower_high = self._state.initial_high

        events.append(
            self._emit("LIQUIDITY", candle, level=promoted_liquidity, promoted_from="temporary")
        )
        events.append(self._emit("BOS", candle, level=bos_level, trend="DOWN"))
        events.append(
            self._emit(
                "HL_LH_CONFIRMED",
                candle,
                level=float(self._state.confirmed_lower_high),
                kind="LOWER_HIGH",
            )
        )

        ob = self._create_ob(
            candle=self._state.initial_candle,  # type: ignore[arg-type]
            direction="SHORT",
            hl_lh_level=float(self._state.confirmed_lower_high),
            bos_level=bos_level,
            liquidity_level=promoted_liquidity,
            is_first=True,
            trigger_candle=candle,
        )
        events.append(self._emit_ob_new(ob, candle))
        self._state.phase = MsiPhase.HL_LH_CYCLE
        self._state.pre_bos_phase = PreBosPhase.BUILDING
        self._state.v_cycle = VCyclePhase.WAIT_LIQ_GRAB
        self._state.temporary_low = candle.low
        return events

    def _update_temps_up_building(
        self, candle: MsiCandle, th: float, tl: float
    ) -> Optional[Dict[str, float]]:
        """
        PDF str. 8 (UP): proces budowy Temporary Liquidity.
        - LOW < Temporary Liquidity → przedłużenie procesu (nowy Temp Liq).
        - HIGH > Temporary HIGH → aktualizacja Temp HIGH (dopiero wtedy HIGH liczy się).
        - W pozostałych przypadkach → ignoruj (HIGH wewnątrz zakresu nieistotne).
        """
        changed: Dict[str, float] = {}

        if candle.low < tl:
            self._state.temporary_liquidity = candle.low
            changed["temporary_liquidity"] = candle.low
            if candle.high > th:
                self._state.temporary_high = candle.high
                changed["temporary_high"] = candle.high
            return changed or None

        if candle.high > th:
            self._state.temporary_high = candle.high
            changed["temporary_high"] = candle.high
            return changed

        return None

    def _update_temps_down_building(
        self, candle: MsiCandle, tlow: float, tliq: float
    ) -> Optional[Dict[str, float]]:
        """
        PDF str. 8 (DOWN) — symetrycznie:
        - HIGH > Temporary Liquidity → przedłużenie procesu.
        - LOW < Temporary LOW → aktualizacja Temp LOW.
        - Inaczej ignoruj.
        """
        changed: Dict[str, float] = {}

        if candle.high > tliq:
            self._state.temporary_liquidity = candle.high
            changed["temporary_liquidity"] = candle.high
            if candle.low < tlow:
                self._state.temporary_low = candle.low
                changed["temporary_low"] = candle.low
            return changed or None

        if candle.low < tlow:
            self._state.temporary_low = candle.low
            changed["temporary_low"] = candle.low
            return changed

        return None

    # ------------------------------------------------------------------ #
    # Sekcja V — kolejne OB (cykl HL/LH)
    # UP: grab LIQUIDITY → BOS HIGH → Temp Higher LOW → aż BOS HIGH przełamany
    #     → ostatni Temp Higher LOW = HIGHER LOW → świeca oznaczająca = nowy OB.
    # DOWN — symetrycznie.
    # ------------------------------------------------------------------ #

    def _process_hl_lh_cycle(self, candle: MsiCandle) -> List[StructureEvent]:
        if self._state.initial_trend == Trend.UP:
            return self._v_cycle_up(candle)
        return self._v_cycle_down(candle)

    def _v_cycle_up(self, candle: MsiCandle) -> List[StructureEvent]:
        events: List[StructureEvent] = []
        liq = self._state.liquidity
        assert liq is not None

        if self._state.v_cycle == VCyclePhase.WAIT_LIQ_GRAB:
            # Step 1: LOW poniżej marked LIQUIDITY
            if candle.low < liq:
                swept = liq
                self._state.cycle_swept_liquidity = swept
                self._state.liquidity = candle.low
                events.append(
                    self._emit("LIQUIDITY_GRAB", candle, swept_level=swept, new_liquidity=candle.low)
                )
                # Step 2: Temporary HIGH → BOS HIGH
                bos_h = self._state.temporary_high
                if bos_h is None:
                    bos_h = candle.high
                self._state.bos_high = bos_h
                # Step 3: LOW Breaking Candle → Temporary Higher LOW
                self._state.temporary_higher_low = candle.low
                self._state.hl_marking_candle = candle
                self._state.v_cycle = VCyclePhase.UPDATING_TEMP_HL
                events.append(self._emit("BOS_HIGH_SET", candle, level=bos_h))
            elif self._state.temporary_high is not None and candle.high > self._state.temporary_high:
                self._state.temporary_high = candle.high
                events.append(self._emit("TEMP_UPDATE", candle, temporary_high=candle.high))
            return events

        # Step 4–5: aktualizuj Temp Higher LOW aż BOS HIGH przełamany
        bos_h = self._state.bos_high
        assert bos_h is not None

        if candle.high > bos_h:
            # Step 5: BOS HIGH przełamany → ostatni Temp Higher LOW = HIGHER LOW
            self._state.confirmed_higher_low = self._state.temporary_higher_low
            marking = self._state.hl_marking_candle
            if marking is None:
                marking = candle
            events.append(
                self._emit(
                    "HL_LH_CONFIRMED",
                    candle,
                    level=float(self._state.confirmed_higher_low),
                    kind="HIGHER_LOW",
                )
            )
            # Step 6: świeca która oznaczyła HL = nowa IC = nowy OB
            events.extend(self._mark_subsequent_ob_up(marking, candle, bos_h))
            return events

        # Step 4: kolejne breaking candles → niższy Temp Higher LOW
        if self._state.temporary_higher_low is None or candle.low < self._state.temporary_higher_low:
            self._state.temporary_higher_low = candle.low
            self._state.hl_marking_candle = candle

        if self._state.temporary_high is not None and candle.high > self._state.temporary_high:
            self._state.temporary_high = candle.high

        return events

    def _mark_subsequent_ob_up(
        self, marking: MsiCandle, trigger: MsiCandle, bos_h: float
    ) -> List[StructureEvent]:
        events: List[StructureEvent] = []
        liq_level = self._state.cycle_swept_liquidity
        if liq_level is None:
            liq_level = self._state.liquidity

        self._set_initial_candle(marking)
        ob = self._create_ob(
            candle=marking,
            direction="LONG",
            hl_lh_level=float(self._state.confirmed_higher_low),  # type: ignore[arg-type]
            bos_level=bos_h,
            liquidity_level=float(liq_level),  # type: ignore[arg-type]
            is_first=False,
            trigger_candle=trigger,
        )
        events.append(self._emit_ob_new(ob, trigger))

        self._state.temporary_high = trigger.high
        self._state.temporary_liquidity = marking.low
        self._state.bos_high = trigger.high
        self._state.v_cycle = VCyclePhase.WAIT_LIQ_GRAB
        self._state.temporary_higher_low = None
        self._state.hl_marking_candle = None
        self._state.cycle_swept_liquidity = None
        return events

    def _v_cycle_down(self, candle: MsiCandle) -> List[StructureEvent]:
        events: List[StructureEvent] = []
        liq = self._state.liquidity
        assert liq is not None

        if self._state.v_cycle == VCyclePhase.WAIT_LIQ_GRAB:
            # Step 1: HIGH powyżej marked LIQUIDITY
            if candle.high > liq:
                swept = liq
                self._state.cycle_swept_liquidity = swept
                self._state.liquidity = candle.high
                events.append(
                    self._emit("LIQUIDITY_GRAB", candle, swept_level=swept, new_liquidity=candle.high)
                )
                bos_l = self._state.temporary_low
                if bos_l is None:
                    bos_l = candle.low
                self._state.bos_low = bos_l
                self._state.temporary_lower_high = candle.high
                self._state.lh_marking_candle = candle
                self._state.v_cycle = VCyclePhase.UPDATING_TEMP_HL
                events.append(self._emit("BOS_LOW_SET", candle, level=bos_l))
            elif self._state.temporary_low is not None and candle.low < self._state.temporary_low:
                self._state.temporary_low = candle.low
                events.append(self._emit("TEMP_UPDATE", candle, temporary_low=candle.low))
            return events

        bos_l = self._state.bos_low
        assert bos_l is not None

        if candle.low < bos_l:
            self._state.confirmed_lower_high = self._state.temporary_lower_high
            marking = self._state.lh_marking_candle
            if marking is None:
                marking = candle
            events.append(
                self._emit(
                    "HL_LH_CONFIRMED",
                    candle,
                    level=float(self._state.confirmed_lower_high),
                    kind="LOWER_HIGH",
                )
            )
            events.extend(self._mark_subsequent_ob_down(marking, candle, bos_l))
            return events

        if self._state.temporary_lower_high is None or candle.high > self._state.temporary_lower_high:
            self._state.temporary_lower_high = candle.high
            self._state.lh_marking_candle = candle

        if self._state.temporary_low is not None and candle.low < self._state.temporary_low:
            self._state.temporary_low = candle.low

        return events

    def _mark_subsequent_ob_down(
        self, marking: MsiCandle, trigger: MsiCandle, bos_l: float
    ) -> List[StructureEvent]:
        events: List[StructureEvent] = []
        liq_level = self._state.cycle_swept_liquidity
        if liq_level is None:
            liq_level = self._state.liquidity

        self._set_initial_candle(marking)
        ob = self._create_ob(
            candle=marking,
            direction="SHORT",
            hl_lh_level=float(self._state.confirmed_lower_high),  # type: ignore[arg-type]
            bos_level=bos_l,
            liquidity_level=float(liq_level),  # type: ignore[arg-type]
            is_first=False,
            trigger_candle=trigger,
        )
        events.append(self._emit_ob_new(ob, trigger))

        self._state.temporary_low = trigger.low
        self._state.temporary_liquidity = marking.high
        self._state.bos_low = trigger.low
        self._state.v_cycle = VCyclePhase.WAIT_LIQ_GRAB
        self._state.temporary_lower_high = None
        self._state.lh_marking_candle = None
        self._state.cycle_swept_liquidity = None
        return events

    # ------------------------------------------------------------------ #
    # Sekcja VI — CHOCH (Change of Character)
    # PDF: LOW < Higher LOW (UP) / HIGH > Lower HIGH (DOWN) bieżącego OB.
    # Opcja A: kontynuacja. Opcja B: odwrócenie → nowy OB przeciwny.
    # ------------------------------------------------------------------ #

    def _detect_choch(self, candle: MsiCandle) -> Optional[StructureEvent]:
        ob = self._state.current_ob
        if ob is None:
            return None

        if self._state.initial_trend == Trend.UP and candle.low < ob.hl_lh_level:
            self._state.choch_candle = candle
            self._state.choch_mode = ChochMode.PENDING
            self._state.choch_low_intact = True
            self._state.phase = MsiPhase.CHOCH_PENDING
            self._state.bos_high = candle.high
            self._state.temporary_high = candle.high
            return self._emit(
                "CHOCH",
                candle,
                mode="PENDING",
                hl_lh_level=ob.hl_lh_level,
                trend="UP",
                bos_high=candle.high,
            )

        if self._state.initial_trend == Trend.DOWN and candle.high > ob.hl_lh_level:
            self._state.choch_candle = candle
            self._state.choch_mode = ChochMode.PENDING
            self._state.choch_high_intact = True
            self._state.phase = MsiPhase.CHOCH_PENDING
            self._state.bos_low = candle.low
            self._state.temporary_low = candle.low
            return self._emit(
                "CHOCH",
                candle,
                mode="PENDING",
                hl_lh_level=ob.hl_lh_level,
                trend="DOWN",
                bos_low=candle.low,
            )

        return None

    def _process_choch(self, candle: MsiCandle) -> List[StructureEvent]:
        events: List[StructureEvent] = []
        if self._state.choch_mode != ChochMode.PENDING or self._state.choch_candle is None:
            return events

        if self._state.initial_trend == Trend.UP:
            events.extend(self._choch_up(candle))
        else:
            events.extend(self._choch_down(candle))
        return events

    def _choch_up(self, candle: MsiCandle) -> List[StructureEvent]:
        events: List[StructureEvent] = []
        choch = self._state.choch_candle
        assert choch is not None
        bos_h = self._state.bos_high
        assert bos_h is not None

        if candle.low < choch.low:
            self._state.choch_low_intact = False

        if self._state.choch_low_intact and candle.high > bos_h:
            self._state.choch_mode = ChochMode.NO_REVERSAL
            self._state.phase = MsiPhase.HL_LH_CYCLE
            self._state.temporary_higher_low = choch.low
            self._state.hl_marking_candle = choch
            self._state.v_cycle = VCyclePhase.UPDATING_TEMP_HL
            self._state.choch_candle = None
            events.append(
                self._emit(
                    "CHOCH",
                    candle,
                    mode="NO_REVERSAL",
                    trend="UP",
                    temporary_higher_low=choch.low,
                )
            )
            return events

        rev = self._try_choch_reversal_down(candle)
        if rev:
            events.extend(rev)
        return events

    def _choch_down(self, candle: MsiCandle) -> List[StructureEvent]:
        events: List[StructureEvent] = []
        choch = self._state.choch_candle
        assert choch is not None
        bos_l = self._state.bos_low
        assert bos_l is not None

        if candle.high > choch.high:
            self._state.choch_high_intact = False

        if self._state.choch_high_intact and candle.low < bos_l:
            self._state.choch_mode = ChochMode.NO_REVERSAL
            self._state.phase = MsiPhase.HL_LH_CYCLE
            self._state.temporary_lower_high = choch.high
            self._state.lh_marking_candle = choch
            self._state.v_cycle = VCyclePhase.UPDATING_TEMP_HL
            self._state.choch_candle = None
            events.append(
                self._emit(
                    "CHOCH",
                    candle,
                    mode="NO_REVERSAL",
                    trend="DOWN",
                    temporary_lower_high=choch.high,
                )
            )
            return events

        rev = self._try_choch_reversal_up(candle)
        if rev:
            events.extend(rev)
        return events

    def _try_choch_reversal_down(self, candle: MsiCandle) -> List[StructureEvent]:
        """Opcja B (UP → DOWN): po CHOCH, gdy LOW < CHOCH LOW → BOS HIGH → Lower HIGH → OB SHORT."""
        choch = self._state.choch_candle
        ob = self._state.current_ob
        if choch is None or ob is None:
            return []

        if self._state.choch_low_intact:
            return []

        bos_h = self._state.bos_high or choch.high
        if candle.high <= bos_h:
            return []

        if candle.high <= ob.hl_lh_level:
            return []

        self._state.choch_mode = ChochMode.REVERSAL
        self._state.initial_trend = Trend.DOWN
        self._state.confirmed_lower_high = candle.high
        self._state.liquidity = choch.low
        self._state.bos_low = bos_h

        self._set_initial_candle(choch)
        new_ob = self._create_ob(
            candle=choch,
            direction="SHORT",
            hl_lh_level=candle.high,
            bos_level=bos_h,
            liquidity_level=choch.low,
            is_first=False,
            trigger_candle=candle,
        )
        self._state.phase = MsiPhase.HL_LH_CYCLE
        self._state.v_cycle = VCyclePhase.WAIT_LIQ_GRAB
        self._state.temporary_low = candle.low
        self._state.temporary_liquidity = choch.high
        self._state.choch_mode = ChochMode.NONE
        self._state.choch_candle = None
        self._state.choch_low_intact = True

        return [
            self._emit_ob_new(new_ob, candle),
            self._emit("CHOCH", candle, mode="REVERSAL", new_trend="DOWN", ob_chain_id=new_ob.chain_id),
        ]

    def _try_choch_reversal_up(self, candle: MsiCandle) -> List[StructureEvent]:
        """Opcja B (DOWN → UP): symetrycznie."""
        choch = self._state.choch_candle
        ob = self._state.current_ob
        if choch is None or ob is None:
            return []

        if self._state.choch_high_intact:
            return []

        bos_l = self._state.bos_low or choch.low
        if candle.low >= bos_l:
            return []

        if candle.low >= ob.hl_lh_level:
            return []

        self._state.choch_mode = ChochMode.REVERSAL
        self._state.initial_trend = Trend.UP
        self._state.confirmed_higher_low = candle.low
        self._state.liquidity = choch.high
        self._state.bos_high = bos_l

        self._set_initial_candle(choch)
        new_ob = self._create_ob(
            candle=choch,
            direction="LONG",
            hl_lh_level=candle.low,
            bos_level=bos_l,
            liquidity_level=choch.high,
            is_first=False,
            trigger_candle=candle,
        )
        self._state.phase = MsiPhase.HL_LH_CYCLE
        self._state.v_cycle = VCyclePhase.WAIT_LIQ_GRAB
        self._state.temporary_high = candle.high
        self._state.temporary_liquidity = choch.low
        self._state.choch_mode = ChochMode.NONE
        self._state.choch_candle = None
        self._state.choch_high_intact = True

        return [
            self._emit_ob_new(new_ob, candle),
            self._emit("CHOCH", candle, mode="REVERSAL", new_trend="UP", ob_chain_id=new_ob.chain_id),
        ]

    # ------------------------------------------------------------------ #
    # OrderBlock helpers
    # ------------------------------------------------------------------ #

    def _create_ob(
        self,
        candle: MsiCandle,
        direction: str,
        hl_lh_level: float,
        bos_level: float,
        liquidity_level: float,
        is_first: bool,
        trigger_candle: MsiCandle,
    ) -> OrderBlock:
        self._state.chain_seq += 1
        chain_id = f"{self.symbol}-{candle.ts}-{self._state.chain_seq}"
        ob = OrderBlock(
            chain_id=chain_id,
            candle=candle,
            direction=direction,
            hl_lh_level=hl_lh_level,
            bos_level=bos_level,
            liquidity_level=liquidity_level,
            initial_trend=self._state.initial_trend.value if self._state.initial_trend else "UNKNOWN",
            detected_at_ts=trigger_candle.ts,
            is_first=is_first,
        )
        self._state.previous_ob = self._state.current_ob
        self._state.current_ob = ob
        self._all_obs.append(ob)
        return ob

    def _emit_ob_new(self, ob: OrderBlock, trigger: MsiCandle) -> StructureEvent:
        return self._emit(
            "OB_NEW",
            trigger,
            chain_id=ob.chain_id,
            ob_direction=ob.direction,
            ob_high=ob.ob_high,
            ob_low=ob.ob_low,
            ob_height=ob.ob_height,
            ob_candle_ts=ob.candle.ts,
            hl_lh_level=ob.hl_lh_level,
            bos_level=ob.bos_level,
            liquidity_level=ob.liquidity_level,
            initial_trend=ob.initial_trend,
            is_first=ob.is_first,
        )

    def _emit(self, event_type: str, candle: MsiCandle, **kwargs: Any) -> StructureEvent:
        payload = {
            "candle_ts": candle.ts,
            "candle_high": candle.high,
            "candle_low": candle.low,
            **kwargs,
        }
        if self._state.current_ob and event_type != "OB_NEW":
            payload.setdefault("current_ob_chain_id", self._state.current_ob.chain_id)
        return StructureEvent(
            event_type=event_type,
            symbol=self.symbol,
            ts=candle.ts,
            payload=payload,
        )

    def _dispatch(self, events: List[StructureEvent], ob: Optional[OrderBlock]) -> None:
        if not self._on_event:
            return
        for ev in events:
            ob_for_ev = ob
            if ev.event_type == "OB_NEW":
                ob_for_ev = self._state.current_ob
            try:
                self._on_event(ev, ob_for_ev)
            except Exception as e:
                logger.warning("[%s] MSI event sink error: %s", self.symbol, e)

    @staticmethod
    def _ob_to_dict(ob: Optional[OrderBlock]) -> Optional[Dict[str, Any]]:
        if ob is None:
            return None
        return {
            "chain_id": ob.chain_id,
            "direction": ob.direction,
            "ob_high": ob.ob_high,
            "ob_low": ob.ob_low,
            "ob_height": ob.ob_height,
            "hl_lh_level": ob.hl_lh_level,
            "detected_at_ts": ob.detected_at_ts,
        }
