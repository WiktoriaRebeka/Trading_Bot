# orderflow_engine/metrics_processor.py
# WERSJA: 7.1 - ELITE INSTITUTIONAL ENGINE (Full Logic + Ingestion Layer)

import os
import time
import logging
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple, Any, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

# Importy lokalne
from orderflow_engine.market_structure import MarketStructureEngine 
from orderflow_engine.candle_builder import CandleBuilder
from orderflow_engine.bigquery_logger import OrderFlowBigQueryLogger
from orderflow_engine.confidence_scorer import ConfidenceScorer
from orderflow_engine.integration import update_symbol_context
from orderflow_engine.risk_levels import calculate_structure_risk_levels
from orderflow_engine.settings import get_trading_session, settings
from orderflow_engine.msi_engine import MsiCandle, MsiEngine
from orderflow_engine.msi_event_logger import MsiEventLogger
from orderflow_engine.ob_orderflow_snapshot import VP_HISTORY_MAXLEN
from shared_lib.signal_mode import (
    footprint_alerts_enabled,
    get_signal_mode,
    msi_engine_enabled,
    msi_trade_enabled,
)

logger = logging.getLogger(__name__)

MSI_ENGINE_ENABLED = msi_engine_enabled()


def _coerce_liquidation_ts_ms(raw: int) -> int:
    """Bybit podaje T w ms; jeśli przyjdzie unix w sekundach (< 1e12), skaluj do ms."""
    if raw <= 0:
        return int(time.time() * 1000)
    raw = int(raw)
    if raw < 10**12:
        return raw * 1000
    return raw


@dataclass
class LiquidationEvent:
    symbol: str
    side: str
    price: float
    qty: float
    time: int
    value_usd: float

@dataclass
class OrderBookWall:
    price: float
    size: float
    distance_from_mid: float
    side: str

@dataclass
class DOMSnapshot:
    symbol: str
    bids: List[Tuple[float, float]]
    asks: List[Tuple[float, float]]
    timestamp: int
    obi: float
    best_bid: float
    best_ask: float
    bid_walls: List[OrderBookWall]
    ask_walls: List[OrderBookWall]

class OrderFlowMetrics:
    def __init__(self, firestore_client=None):
        self.firestore = firestore_client
        self.builders = defaultdict(lambda: CandleBuilder(""))
        self.engines = defaultdict(lambda: MarketStructureEngine(lookback_bars=500))
        self.bq_logger = OrderFlowBigQueryLogger()
        self.scorer = ConfidenceScorer()
        self.msi_logger = MsiEventLogger(processor=self) if MSI_ENGINE_ENABLED else None
        self.msi_engines: Dict[str, MsiEngine] = {}
        self._msi_sink = None
        if MSI_ENGINE_ENABLED and self.msi_logger is not None:
            from orderflow_engine.msi_event_sink import MsiEventSink
            self._msi_sink = MsiEventSink(self.msi_logger, self)
        
        self.trades = defaultdict(lambda: deque(maxlen=20000))
        # Bufor zamkniętych świec 1M z wolumenem (ts/low/high/volume) — źródło profilu VP wokół OB.
        # self.trades nie sięga 120 min wstecz dla płynnych symboli, dlatego osobny bufor świecowy.
        self.candle_vol_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=VP_HISTORY_MAXLEN))
        self.tickers = {}
        self.liquidations = defaultdict(list)
        self.orderbook_snapshots = {}
        self.orderbook_levels_50: Dict[str, Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]] = {}
        self.delta_history = defaultdict(lambda: deque(maxlen=200))
        
        self.LIQUIDATION_CASCADE_THRESHOLD_USD = int(
            os.environ.get("LIQ_CASCADE_THRESHOLD_USD", "10000")
        )
        logger.info(f"[MetricsProcessor] LIQ_CASCADE_THRESHOLD_USD={self.LIQUIDATION_CASCADE_THRESHOLD_USD}")
        self.DOM_WALL_MULTIPLIER = 3.5 
        self.SIGNAL_COOLDOWN_SEC = 300
        self.MIN_CONFIDENCE_SCORE = 75 
        self.last_signal_time = defaultdict(float)
        self._liq_ingest_log_ts = defaultdict(float)
        self._liq_first_logged = set()
        self.LIQ_INGEST_LOG_INTERVAL_SEC = float(
            os.environ.get("LIQ_INGEST_LOG_INTERVAL_SEC", "60")
        )
        self.ORDERBOOK_CONTEXT_REFRESH_SEC = float(
            os.environ.get("ORDERBOOK_CONTEXT_REFRESH_SEC", "0.2")
        )
        self._orderbook_ctx_refresh_at: Dict[str, float] = {}

        self.bot_url = os.environ.get(
            "BOT_SERVICE_URL",
            "https://trading-bot-service-785819958951.europe-central2.run.app/process-alerts"
        )
        logger.info(f"[MetricsProcessor] BOT_SERVICE_URL={'env' if os.environ.get('BOT_SERVICE_URL') else 'fallback'}: {self.bot_url}")

        logger.info("✅ OrderFlow V7.1: Institutional Engine Active.")
        logger.info(
            "[SIGNAL_MODE] %s | MSI engine=%s trade=%s | footprint_eval=%s",
            get_signal_mode(),
            MSI_ENGINE_ENABLED,
            msi_trade_enabled(),
            footprint_alerts_enabled(),
        )
        if MSI_ENGINE_ENABLED:
            trade_msg = "trade alerts ON" if msi_trade_enabled() else "trade alerts OFF"
            logger.info("[MSI] Engine enabled (1M structure + %s).", trade_msg)

    def _get_msi_engine(self, symbol: str) -> Optional[MsiEngine]:
        if not MSI_ENGINE_ENABLED or self.msi_logger is None:
            return None
        sym = str(symbol).upper()
        if sym not in self.msi_engines:
            on_event = self._msi_sink.handle_event if self._msi_sink else self.msi_logger.handle_event
            self.msi_engines[sym] = MsiEngine(
                symbol=sym,
                on_event=on_event,
            )
        return self.msi_engines[sym]

    def _feed_msi_candle(self, symbol: str, candle: dict) -> None:
        engine = self._get_msi_engine(symbol)
        if engine is None:
            return
        try:
            engine.on_candle_close(MsiCandle.from_dict(candle))
        except Exception as e:
            logger.error("[MSI] on_candle_close failed symbol=%s: %s", symbol, e, exc_info=True)

    def pre_load_history(self, symbol, history_m1, history_d1):
        symbol = str(symbol).upper()
        engine = self.engines[symbol]
        if history_d1:
            engine.major_high = max([k['high'] for k in history_d1])
            engine.major_low = min([k['low'] for k in history_d1])
        for c in history_m1:
            engine.update_candles(c['open'], c['high'], c['low'], c['close'], c['ts'])
            self._feed_msi_candle(symbol, c)
            # Seed bufora VP z backfillu REST (fetch_history niesie volume=k[5];
            # ścieżka Firestore ma volume=0, więc profil zapełniamy z REST).
            self.candle_vol_history[symbol].append({
                'ts': c['ts'],
                'low': c['low'],
                'high': c['high'],
                'volume': float(c.get('volume', 0.0) or 0.0),
            })

    def process_trade(self, timestamp: int, symbol: str, side: str, qty: float, price: float):
        sym = str(symbol).upper()
        s = str(side).strip()
        if s.lower() == "buy":
            s = "Buy"
        elif s.lower() == "sell":
            s = "Sell"
        self.trades[sym].append({'timestamp': timestamp, 'side': s, 'qty': qty, 'price': price})
        builder = self.builders[sym]
        if not builder.symbol: builder.symbol = sym
        new_candle = builder.process_tick(price, qty, timestamp)
        if new_candle:
            self.engines[sym].update_candles(new_candle['open'], new_candle['high'], new_candle['low'], new_candle['close'], new_candle['ts'])
            self._feed_msi_candle(sym, new_candle)
            # Przechwycenie wolumenu zamkniętej świecy do bufora VP (dotąd porzucany przy update_candles).
            self.candle_vol_history[sym].append({
                'ts': new_candle['ts'],
                'low': new_candle['low'],
                'high': new_candle['high'],
                'volume': new_candle['volume'],
            })
        delta, _, _ = self._calculate_delta_window_volumes(sym, 300)
        self.delta_history[sym].append({'price': price, 'delta': delta, 'timestamp': timestamp})

    def process_ticker(self, symbol, price, funding_rate, open_interest, volume_24h):
        sym = str(symbol).upper()
        prev = self.tickers.get(sym, {})
        try:
            p = float(price) if price is not None else 0.0
        except (TypeError, ValueError):
            p = 0.0
        if p <= 0 and prev.get("price"):
            p = float(prev["price"])
        self.tickers[sym] = {
            'price': p, 'funding_rate': funding_rate,
            'open_interest': open_interest, 'volume_24h': volume_24h
        }
        # To wywołanie gwarantuje, że bot_service widzi aktualną cenę
        self._refresh_context_cache(sym) 
        self._autonomous_scanner(sym)

    def process_liquidation(self, liq):
        logger.debug(
            "[LIQ_RAW] symbol=%s side=%s price=%s qty=%s time=%s",
            liq.get("symbol"), liq.get("side"), liq.get("price"), liq.get("qty"), liq.get("time"),
        )
        sym = str(liq.get("symbol") or "").strip().upper()
        if not sym:
            logger.warning("[LiqIngest] Pominięto zdarzenie — brak symbolu: %s", liq)
            return
        raw_side = liq.get("side")
        if raw_side is None or str(raw_side).strip() == "":
            logger.warning("[LiqIngest] Pominięto %s — brak side (S)", sym)
            return
        side = str(raw_side).strip()
        try:
            raw_t = int(float(liq.get("time", 0) or 0))
        except (TypeError, ValueError):
            raw_t = 0
        t_ms = _coerce_liquidation_ts_ms(raw_t)
        try:
            px = float(liq.get("price", 0) or 0)
            qty = float(liq.get("qty", 0) or 0)
        except (TypeError, ValueError):
            logger.warning("[LiqIngest] Pominięto %s — nieprawidłowy price/qty: %s", sym, liq)
            return
        event = LiquidationEvent(sym, side, px, qty, t_ms, qty * px)
        self.liquidations[event.symbol].append(event)
        cutoff = int(time.time() * 1000) - 300000
        self.liquidations[event.symbol] = [e for e in self.liquidations[event.symbol] if e.time > cutoff]
        self._check_liquidation_cascade(event.symbol)
        self._refresh_context_cache(event.symbol)
        if sym not in self._liq_first_logged:
            self._liq_first_logged.add(sym)
            logger.info(
                "[LiqIngest] FIRST_EVENT symbol=%s usd=%.2f side=%s — WS→processor OK",
                sym, event.value_usd, event.side,
            )
        now_m = time.time()
        if now_m - self._liq_ingest_log_ts[sym] >= self.LIQ_INGEST_LOG_INTERVAL_SEC:
            self._liq_ingest_log_ts[sym] = now_m
            buf = self.liquidations[sym]
            total_usd = sum(e.value_usd for e in buf)
            logger.info(
                "[LiqIngest] symbol=%s buffer_events=%d buffer_total_usd=%.2f last_event_usd=%.2f side=%s",
                sym, len(buf), total_usd, event.value_usd, event.side,
            )

    @staticmethod
    def _sort_ob_side(levels: List[Tuple[float, float]], *, bids_side: bool, depth: int) -> List[Tuple[float, float]]:
        if not levels:
            return []
        key = (lambda x: -x[0]) if bids_side else (lambda x: x[0])
        return sorted(levels, key=key)[:depth]

    def _merge_ob_side(
        self,
        prev: List[Tuple[float, float]],
        updates: Iterable[Tuple[float, float]],
        *,
        bids_side: bool,
        depth: int = 50,
    ) -> List[Tuple[float, float]]:
        book: Dict[float, float] = {float(p): float(q) for p, q in prev}
        for p, q in updates:
            pf, qf = float(p), float(q)
            if qf <= 0:
                book.pop(pf, None)
            else:
                book[pf] = qf
        items = sorted(book.items(), key=lambda x: (-x[0] if bids_side else x[0]))
        return [(p, q) for p, q in items[:depth]]

    def process_orderbook(self, ob_data: dict):
        symbol = str(ob_data["symbol"]).upper()
        msg_type = str(ob_data.get("msg_type") or "snapshot").lower()
        raw_b: List[Tuple[float, float]] = list(ob_data.get("bids") or [])
        raw_a: List[Tuple[float, float]] = list(ob_data.get("asks") or [])
        prev_full = self.orderbook_levels_50.get(symbol)

        if msg_type == "delta":
            if prev_full is None:
                return
            pb, pa = prev_full
            merged_b = self._merge_ob_side(list(pb), raw_b, bids_side=True, depth=50)
            merged_a = self._merge_ob_side(list(pa), raw_a, bids_side=False, depth=50)
        else:
            merged_b = self._sort_ob_side(raw_b, bids_side=True, depth=50)
            merged_a = self._sort_ob_side(raw_a, bids_side=False, depth=50)

        if not merged_b or not merged_a:
            return
        self.orderbook_levels_50[symbol] = (merged_b, merged_a)
        bids, asks = merged_b[:10], merged_a[:10]
        bid_vol = sum([p * q for p, q in bids[:10]])
        ask_vol = sum([p * q for p, q in asks[:10]])
        obi = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0
        bid_walls = self._detect_walls(bids, 'bid')
        ask_walls = self._detect_walls(asks, 'ask')
        mid_price = (bids[0][0] + asks[0][0]) / 2
        for wall in bid_walls: wall.distance_from_mid = mid_price - wall.price
        for wall in ask_walls: wall.distance_from_mid = wall.price - mid_price
        snapshot = DOMSnapshot(symbol=symbol, bids=bids[:10], asks=asks[:10], timestamp=ob_data['timestamp'], obi=obi, best_bid=bids[0][0], best_ask=asks[0][0], bid_walls=bid_walls, ask_walls=ask_walls)
        self.orderbook_snapshots[symbol] = snapshot
        now = time.time()
        last_r = self._orderbook_ctx_refresh_at.get(symbol, 0.0)
        if now - last_r >= self.ORDERBOOK_CONTEXT_REFRESH_SEC:
            self._orderbook_ctx_refresh_at[symbol] = now
            self._refresh_context_cache(symbol)

    def _refresh_context_cache(self, symbol: str):
        """WARSTWA INGERENCJI: Aktualizuje globalny cache w integration.py"""
        sym = str(symbol).upper()
        try:
            ticker = self.tickers.get(sym, {})
            engine = self.engines[sym]
            dom = self.orderbook_snapshots.get(sym)
            if dom:
                dom_payload = {
                    "obi": dom.obi,
                    "bids": [[p, q] for p, q in dom.bids],
                    "asks": [[p, q] for p, q in dom.asks],
                    "bid_walls": len(dom.bid_walls),
                    "ask_walls": len(dom.ask_walls),
                }
            else:
                dom_payload = {
                    "obi": 0.0,
                    "bids": [],
                    "asks": [],
                    "bid_walls": 0,
                    "ask_walls": 0,
                }
            ctx = {
                "symbol": sym,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "price": ticker.get('price'),
                "funding_rate": ticker.get('funding_rate', 0.0),
                "dom": dom_payload,
                "liquidations": self.get_recent_liquidations(sym),
                "structure": {"last_swing_high": engine.last_swing_high, "last_swing_low": engine.last_swing_low},
                "delta_points": self.get_recent_deltas(sym, limit=5)
            }
            update_symbol_context(sym, ctx)
        except Exception as e:
            logger.error(f"❌ Context Cache Error {sym}: {e}")

    def _autonomous_scanner(self, symbol):
        return  # DISABLED: sygnały tylko przez evaluate_and_maybe_alert
        sym = str(symbol).upper()
        data = self.tickers.get(sym)
        if not data or time.time() - self.last_signal_time[sym] < self.SIGNAL_COOLDOWN_SEC: return
        price = data['price']
        engine = self.engines[sym]
        if engine.is_sweep_happening(price):
            direction = "LONG" if price < (engine.last_swing_low or 0) else "SHORT"
            self._validate_setup_layers(sym, direction, price)

    def _matched_liquidation_buffer_usd(self, sym: str, direction: str) -> float:
        """Zgodnie z signal_detector: LONG → Buy (likwidacja longów), SHORT → Sell."""
        recent = self.liquidations.get(str(sym).upper(), [])
        if direction == "LONG":
            sides = {"buy", "Buy", "BUY"}
        else:
            sides = {"sell", "Sell", "SELL"}
        return sum(e.value_usd for e in recent if str(e.side) in sides)

    def _validate_setup_layers(self, symbol, direction, price):
        sym = str(symbol).upper()

        session = get_trading_session()
        if session in settings.SKIP_SESSIONS:
            logger.info(
                f"⏭️ {sym} SKIPPED: session={session} in SKIP_SESSIONS"
            )
            return

        div = self._detect_delta_divergence(sym)
        delta_strength = float(div.get("strength", 0) or 0)
        if settings.REQUIRE_ZERO_DELTA and delta_strength != 0:
            logger.info(
                f"⏭️ {sym} SKIPPED: delta_strength={delta_strength:.3f} != 0 "
                f"(REQUIRE_ZERO_DELTA=True)"
            )
            return

        liq_vol = self._matched_liquidation_buffer_usd(sym, direction)
        dom = self.orderbook_snapshots.get(sym)
        confidence = self.scorer.calculate({
            'liquidation_volume_usd': liq_vol, 'delta_divergence': div['detected'], 'delta_strength': div.get('strength', 0),
            'obi': abs(dom.obi) if dom else 0, 'dom_wall_detected': len(dom.bid_walls if direction == "LONG" else dom.ask_walls) > 0 if dom else False,
            'structure_strength': self.engines[sym].get_swing_strength(direction), 'funding_rate': self.tickers[sym].get('funding_rate', 0), 'direction': direction
        })
        if confidence >= self.MIN_CONFIDENCE_SCORE and liq_vol >= self.LIQUIDATION_CASCADE_THRESHOLD_USD:
            self.last_signal_time[sym] = time.time()
            entry_price = dom.best_bid if (dom and direction == "LONG") else price
            import asyncio
            asyncio.create_task(self._execute_signal_async(sym, direction, entry_price, confidence, liq_vol, div))

    async def _execute_signal_async(self, symbol: str, direction: str, level: float, score: float, liq_v: float, div: dict):
        import aiohttp
        event_id = f"PY-{symbol}-{int(time.time())}"
        risk_levels = calculate_structure_risk_levels(
            symbol=symbol,
            direction=direction,
            entry_price=level,
            engine=self.engines[symbol],
            confidence=score,
            logger=logger,
        )
        if risk_levels is None:
            logger.warning(f"⚠️ {symbol} {direction} alert skipped: no valid structure-based risk levels")
            return
        payload = {
            "event_id": event_id, "signal_id": f"AUTO-{event_id}", "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "direction": direction, "entry": level,
            "sl": risk_levels.sl,
            "tp": risk_levels.tp,
            "risk_pct": risk_levels.risk_pct, "rr": risk_levels.rr, "structure_state": 1 if direction == "LONG" else -1, "risk_usdt": 2.5,
            "raw_context": {
                "confidence_score": score,
                "liq_volume_usd": liq_v,
                "delta_div_detected": div['detected'],
                "delta_strength": div.get('strength', 0),
                "swept_swing_level": risk_levels.swing_level,
                "structure_sl_fallback_used": risk_levels.fallback_used,
                "structure_tp_capped": risk_levels.tp_capped,
            }
        }
        BOT_URL = self.bot_url
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(BOT_URL, json=payload, timeout=5) as resp:
                    if resp.status == 200:
                        logger.info(
                            f"🚀 SYGNAŁ_OK | ALERT SENT SUCCESSFULLY [autonomous] {symbol} {direction} "
                            f"| event_id={event_id} | score={score:.1f} | liq_usd={liq_v:.2f}"
                        )
                    else:
                        body = await resp.text()
                        logger.error(
                            "❌ autonomous alert HTTP %s dla %s: %s",
                            resp.status, symbol, body[:500],
                        )
        except Exception as e: logger.error(f"❌ Błąd komunikacji async: {e}")

    def _calculate_delta_window_volumes(self, symbol, seconds=300):
        sym = str(symbol).upper()
        cutoff = int(time.time() * 1000) - (seconds * 1000)
        recent = [t for t in self.trades[sym] if t['timestamp'] > cutoff]
        buy_v = sum(t['qty'] * t['price'] for t in recent if str(t['side']).lower() == 'buy')
        sell_v = sum(t['qty'] * t['price'] for t in recent if str(t['side']).lower() == 'sell')
        return buy_v - sell_v, buy_v, sell_v

    def _calculate_delta_window(self, symbol, seconds):
        delta, _, _ = self._calculate_delta_window_volumes(symbol, seconds)
        return delta

    def _count_trades_window(self, symbol, seconds=300) -> int:
        sym = str(symbol).upper()
        cutoff = int(time.time() * 1000) - (seconds * 1000)
        return sum(1 for t in self.trades[sym] if t['timestamp'] > cutoff)

    def _detect_walls(self, levels, side):
        if not levels or len(levels) < 10: return []
        avg_s = sum([q for _, q in levels[:20]]) / 20
        walls = []
        for p, q in levels[:20]:
            if q > avg_s * self.DOM_WALL_MULTIPLIER: walls.append(OrderBookWall(p, q, 0, side))
        return walls

    def _detect_delta_divergence(self, symbol):
        sym = str(symbol).upper()
        hist = list(self.delta_history[sym])
        if len(hist) < 30: return {'detected': False}
        recent = hist[-30:]; prices = [d['price'] for d in recent]; deltas = [d['delta'] for d in recent]
        if prices[-1] < min(prices[-10:-1]) and deltas[-1] > min(deltas[-10:-1]): return {'detected': True, 'type': 'BULLISH', 'strength': abs(deltas[-1] - min(deltas[-10:-1]))}
        if prices[-1] > max(prices[-10:-1]) and deltas[-1] < max(deltas[-10:-1]): return {'detected': True, 'type': 'BEARISH', 'strength': abs(deltas[-1] - max(deltas[-10:-1]))}
        return {'detected': False}

    def _check_liquidation_cascade(self, symbol):
        liqs = self.liquidations.get(symbol, [])
        if not liqs: return
        total = sum([e.value_usd for e in liqs])
        # Kaskady likwidacji nadal w buforze in-memory (footprint); bez zapisu do BQ (tabela nie istnieje).
        if total > self.LIQUIDATION_CASCADE_THRESHOLD_USD:
            pass

    def get_last_price(self, symbol: str) -> Optional[float]:
        t = self.tickers.get(str(symbol).upper()); return t['price'] if t else None

    def get_last_funding(self, symbol: str) -> float:
        t = self.tickers.get(str(symbol).upper()); return t.get('funding_rate', 0.0) if t else 0.0

    def get_recent_liquidations(self, symbol: str, window_sec: int = 300):
        sym = str(symbol).upper()
        now_ms = int(time.time() * 1000); cutoff = now_ms - (window_sec * 1000); liqs = self.liquidations.get(sym, [])
        return [{
            'side': e.side,
            'volume_usd': e.value_usd,
            'timestamp': datetime.fromtimestamp(e.time / 1000, tz=timezone.utc).isoformat(),
            'T': str(e.time),
            'time': e.time,
        } for e in liqs if e.time >= cutoff]
    def get_recent_deltas(self, symbol: str, limit: int = 10):
        sym = str(symbol).upper()
        hist = list(self.delta_history[sym])[-limit:]
        return [{
            'price': d['price'], 
            'delta': d['delta'], 
            'timestamp': datetime.fromtimestamp(d['timestamp'] / 1000, tz=timezone.utc).isoformat() # FIX: ISO string
        } for d in hist]
    def get_dom_snapshot(self, symbol: str):
        sym = str(symbol).upper()
        snap = self.orderbook_snapshots.get(sym)
        if not snap:
            return {
                'bids': [], 'asks': [], 'obi': 0.0,
                'best_bid': None, 'best_ask': None,
                'bid_walls': [], 'ask_walls': [],
            }
        def _wall_dict(w):
            return {'price': w.price, 'size': w.size, 'distance_from_mid': w.distance_from_mid}
        return {
            'bids': snap.bids,
            'asks': snap.asks,
            'obi': snap.obi,
            'best_bid': snap.best_bid,
            'best_ask': snap.best_ask,
            'bid_walls': [_wall_dict(w) for w in snap.bid_walls],
            'ask_walls': [_wall_dict(w) for w in snap.ask_walls],
        }

    def get_msi_state(self, symbol: str) -> Optional[dict]:
        engine = self.msi_engines.get(str(symbol).upper())
        if engine is None:
            return None
        return engine.get_state_snapshot()

    def get_full_context(self, symbol):
        sym = str(symbol).upper()
        engine = self.engines[sym]
        return {"symbol": sym, "structure": {"last_swing_high": engine.last_swing_high, "last_swing_low": engine.last_swing_low}, "dom": {"obi": self.orderbook_snapshots[sym].obi if sym in self.orderbook_snapshots else 0}, "ticker": self.tickers.get(sym)}
