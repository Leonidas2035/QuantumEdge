"""EventDrivenWakeupGate over MarketDataHub ZMQ PUB with RAM-First RingMarketBuffer & BufferedJsonlArchiver.

Subscribes to the existing MarketDataHubService ZMQ PUB socket (:5555, supporting
both msgspec JSON and MessagePack frames) with zero secondary WebSocket clients
and zero LLM tokens.
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import msgspec
import zmq
import zmq.asyncio

from quantum_edge_core.contracts.optimization import DecisionEventV1
from quantum_edge_core.execution.storage_policy import (
    BufferedJsonlArchiver,
    RingMarketBuffer,
)

ZMQ_SUB_PREFIXES: tuple[bytes, ...] = (
    b"trade.",
    b"depth.",
    b"orderbook.",
    b"market.depth.",
    b"kline.",
    b"liquidation.",
)


def decode_zmq_payload(raw_bytes: bytes) -> dict[str, Any]:
    """Decode MarketDataHub ZMQ frame (supports both msgspec JSON / UTF-8 JSON and MessagePack)."""
    stripped = raw_bytes.lstrip()
    if stripped.startswith(b"{"):
        obj = msgspec.json.decode(raw_bytes)
        if isinstance(obj, dict):
            return obj
    try:
        obj = msgspec.msgpack.decode(raw_bytes)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    obj = json.loads(raw_bytes.decode("utf-8"))
    if isinstance(obj, dict):
        return obj
    raise ValueError("Decoded ZMQ payload is not a mapping")


@dataclass(slots=True)
class HawkesTracker:
    """Exact O(1) recursive intensity tracker for an exponential-kernel Hawkes process."""

    mu: float = 2.0  # Baseline trade arrival rate (events/s)
    alpha: float = 0.9  # Jump intensity (branching ratio alpha/beta = 0.6 < 1.0)
    beta: float = 1.5  # Exponential decay rate (1/s)
    r_state: float = 0.0
    last_ts: float = 0.0

    def __post_init__(self) -> None:
        if self.mu <= 0.0 or self.alpha < 0.0 or self.beta <= 0.0:
            raise ValueError("Hawkes parameters require mu > 0, alpha >= 0, beta > 0")
        if self.alpha >= self.beta:
            raise ValueError(
                f"Non-stationary Hawkes process: alpha={self.alpha} >= beta={self.beta}"
            )

    def on_trade(self, ts_sec: float) -> float:
        if self.last_ts > 0.0 and ts_sec > self.last_ts:
            self.r_state *= math.exp(-self.beta * (ts_sec - self.last_ts))
        self.r_state += 1.0
        self.last_ts = max(self.last_ts, ts_sec)
        return (self.mu + self.alpha * self.r_state) / self.mu

    def current_ratio(self, now_sec: float) -> float:
        if self.last_ts <= 0.0:
            return 1.0
        dt = max(0.0, now_sec - self.last_ts)
        return (self.mu + self.alpha * self.r_state * math.exp(-self.beta * dt)) / self.mu


@dataclass(slots=True)
class SymmetricCusumFilter:
    """Symmetric CUSUM filter of log-price innovations with adaptive EWMA volatility threshold."""

    k_mult: float = 3.0
    ewma_lambda: float = 0.94
    k_drift: float = 0.05
    min_sigma: float = 0.0005  # 5 bps floor against micro-noise
    last_price: float = 0.0
    ewma_var: float = 2.5e-7
    ewma_mean: float = 0.0
    s_pos: float = 0.0
    s_neg: float = 0.0

    def update(self, price: float, *, min_h: float = 0.0) -> str | None:
        if price <= 0.0:
            return None
        if self.last_price <= 0.0:
            self.last_price = price
            return None
        y = math.log(price / self.last_price)
        self.last_price = price
        innov = y - self.ewma_mean
        h = max(self.k_mult * max(math.sqrt(self.ewma_var), self.min_sigma), min_h)
        self.ewma_mean = self.ewma_lambda * self.ewma_mean + (1.0 - self.ewma_lambda) * y
        self.ewma_var = self.ewma_lambda * self.ewma_var + (1.0 - self.ewma_lambda) * (
            innov**2
        )
        self.s_pos = max(0.0, self.s_pos + innov - self.k_drift * h)
        self.s_neg = min(0.0, self.s_neg + innov + self.k_drift * h)
        if self.s_pos > h:
            self.s_pos = 0.0
            return "CUSUM_UP"
        if self.s_neg < -h:
            self.s_neg = 0.0
            return "CUSUM_DOWN"
        return None


class ShadowWakeupLogger:
    """RAM-buffered shadow wakeup logger backed by storage_policy.BufferedJsonlArchiver."""

    def __init__(
        self,
        path: Path | str = Path("runtime/wakeup_shadow_log.jsonl"),
        *,
        flush_max_items: int = 100,
        flush_interval_sec: float = 30.0,
    ) -> None:
        self.path = Path(path)
        self._archiver = BufferedJsonlArchiver(
            self.path,
            flush_max_items=flush_max_items,
            flush_interval_sec=flush_interval_sec,
        )

    @property
    def pending_count(self) -> int:
        return self._archiver.pending_count

    def log(
        self,
        *,
        should_wake: bool,
        did_wake: bool,
        reasons: list[str],
        symbols: list[str],
        metrics: dict[str, Any],
        ts_utc: str | None = None,
    ) -> bool:
        row = {
            "ts_utc": ts_utc or datetime.now(timezone.utc).isoformat(),
            "event_should_wake": should_wake,
            "event_did_wake": did_wake,
            "reasons": reasons,
            "symbols": symbols,
            "metrics": metrics,
        }
        return self._archiver.append(row)

    def flush(self) -> int:
        return self._archiver.flush()

    def close(self) -> int:
        return self._archiver.close()


class EventDrivenWakeupGate:
    """Unified Event Gate over MarketDataHub ZMQ PUB (5 triggers, 90s cooldown, 700ms coalescing)."""

    def __init__(
        self,
        on_decision_event: Callable[[DecisionEventV1], Awaitable[None]],
        *,
        shadow_mode: bool = False,
        coalesce_ms: int = 700,
        per_symbol_cooldown_sec: float = 90.0,
        hawkes_threshold: float = 3.0,
        cvd_z_threshold: float = 2.5,
        imbalance_threshold: float = 0.75,
        hmm_delta_threshold: float = 0.25,
        hysteresis_reset_ratio: float = 0.85,
        ring_buffer_maxlen: int = 256,
        virtual_clock: bool = False,
        shadow_logger: ShadowWakeupLogger | None = None,
        on_shadow_event: Callable[[DecisionEventV1], Awaitable[None]] | None = None,
    ) -> None:
        self.on_decision_event = on_decision_event
        self.on_shadow_event = on_shadow_event
        self.shadow_mode = shadow_mode
        self.coalesce_sec = coalesce_ms / 1000.0
        self.cooldown_sec = per_symbol_cooldown_sec
        self.hawkes_threshold = hawkes_threshold
        self.cvd_z_threshold = cvd_z_threshold
        self.imbalance_threshold = imbalance_threshold
        self.hmm_delta_threshold = hmm_delta_threshold
        self.hysteresis_reset_ratio = hysteresis_reset_ratio
        self.ring_buffer_maxlen = ring_buffer_maxlen
        self.virtual_clock = virtual_clock
        self.shadow_logger = shadow_logger or ShadowWakeupLogger()

        self._cusum: dict[str, SymmetricCusumFilter] = {}
        self._hawkes: dict[str, HawkesTracker] = {}
        self._tick_buffers: dict[str, RingMarketBuffer[dict[str, Any]]] = {}
        self._latched_flags: dict[str, set[str]] = {}
        self._last_wake_by_sym: dict[str, float] = {}
        self._last_heartbeat_hour: int = -1

        self._pending_symbols: set[str] = set()
        self._pending_reasons: list[str] = []
        self._pending_priority: Literal["NORMAL", "CRITICAL"] = "NORMAL"
        self._pending_metrics: dict[str, Any] = {}
        self._pending_opened_ts: float | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def get_recent_ticks(self, symbol: str) -> list[dict[str, Any]]:
        """Return buffered in-RAM recent tick snapshots for ``symbol``."""
        buf = self._tick_buffers.get(symbol.upper())
        return buf.snapshot() if buf is not None else []

    @staticmethod
    def check_liq_magnet(
        *,
        dist_bps: float,
        spread_bps: float,
        atr_15m_bps: float,
        cluster_usd: float,
    ) -> bool:
        if cluster_usd < 500_000.0:
            return False
        if not (dist_bps <= 35.0 or dist_bps <= 1.2 * atr_15m_bps):
            return False
        if dist_bps < 2.5 * spread_bps:
            return False
        return True

    async def ingest_tick(
        self,
        *,
        symbol: str,
        price: float,
        ts_sec: float,
        is_trade: bool = False,
        spread_bps: float = 0.0,
        atr_15m_bps: float = 40.0,
        nearest_liq_dist_bps: float | None = None,
        nearest_liq_usd: float = 0.0,
        cvd_zscore_5m: float = 0.0,
        imbalance_top10: float = 0.0,
        hmm_regime_delta: float = 0.0,
        thesis_invalidated: bool = False,
        position_r_multiple: float | None = None,
    ) -> DecisionEventV1 | None:
        sym = symbol.upper()

        # Flush any virtual-clock coalescing window whose 700ms deadline has elapsed
        emitted_before: DecisionEventV1 | None = None
        if (
            self.virtual_clock
            and self._pending_opened_ts is not None
            and (ts_sec - self._pending_opened_ts) >= self.coalesce_sec
        ):
            emitted_before = await self._emit_pending(ts_sec=self._pending_opened_ts + self.coalesce_sec)

        buf = self._tick_buffers.get(sym)
        if buf is None:
            buf = RingMarketBuffer(maxlen=self.ring_buffer_maxlen)
            self._tick_buffers[sym] = buf
        buf.append(
            {
                "ts_sec": ts_sec,
                "price": price,
                "spread_bps": spread_bps,
                "is_trade": is_trade,
                "imbalance_top10": imbalance_top10,
            }
        )

        cusum = self._cusum.setdefault(sym, SymmetricCusumFilter())
        hawkes = self._hawkes.setdefault(sym, HawkesTracker())
        latched = self._latched_flags.setdefault(sym, set())
        fired: list[str] = []
        priority: Literal["NORMAL", "CRITICAL"] = "NORMAL"

        # 1. CUSUM innovation filter (floor scaled above 1.6x execution spread)
        spread_h_floor = 1.6 * (spread_bps / 10_000.0)
        c_sig = cusum.update(price, min_h=spread_h_floor)
        if c_sig:
            fired.append(f"{sym}:{c_sig}")

        # 2. Hawkes / Orderflow triggers with hysteresis reset band
        h_ratio = hawkes.on_trade(ts_sec) if is_trade else hawkes.current_ratio(ts_sec)
        if h_ratio >= self.hawkes_threshold:
            if "HAWKES" not in latched:
                fired.append(f"{sym}:HAWKES_BURST({h_ratio:.2f}x)")
                latched.add("HAWKES")
        elif h_ratio < self.hawkes_threshold * self.hysteresis_reset_ratio:
            latched.discard("HAWKES")

        # 3. Liquidation Magnet Gate (>= 2.5x spread)
        if nearest_liq_dist_bps is not None and self.check_liq_magnet(
            dist_bps=nearest_liq_dist_bps,
            spread_bps=spread_bps,
            atr_15m_bps=atr_15m_bps,
            cluster_usd=nearest_liq_usd,
        ):
            if "LIQ" not in latched:
                fired.append(f"{sym}:LIQ_MAGNET({nearest_liq_dist_bps:.1f}bps)")
                latched.add("LIQ")
        elif nearest_liq_dist_bps is None or nearest_liq_dist_bps > max(
            42.0, 1.35 * atr_15m_bps
        ):
            latched.discard("LIQ")

        if abs(cvd_zscore_5m) >= self.cvd_z_threshold:
            if "CVD" not in latched:
                fired.append(f"{sym}:CVD_Z({cvd_zscore_5m:+.2f})")
                latched.add("CVD")
        elif abs(cvd_zscore_5m) < self.cvd_z_threshold * self.hysteresis_reset_ratio:
            latched.discard("CVD")

        if abs(imbalance_top10) >= self.imbalance_threshold:
            if "IMB" not in latched:
                fired.append(f"{sym}:OB_IMB({imbalance_top10:+.2f})")
                latched.add("IMB")
        elif abs(imbalance_top10) < self.imbalance_threshold * self.hysteresis_reset_ratio:
            latched.discard("IMB")

        # 4. HMM regime shift / Thesis invalidation / Position R-multiple check
        if hmm_regime_delta >= self.hmm_delta_threshold:
            fired.append(f"{sym}:HMM_SHIFT({hmm_regime_delta:.2f})")
        if thesis_invalidated:
            fired.append(f"{sym}:THESIS_INVALIDATION")
            priority = "CRITICAL"
        if position_r_multiple is not None and (
            position_r_multiple >= 0.6 or position_r_multiple <= -0.5
        ):
            fired.append(f"{sym}:POS_R_CHECK({position_r_multiple:+.2f}R)")
            if position_r_multiple <= -0.5:
                priority = "CRITICAL"

        # 5. Macro 1-hour Heartbeat
        hour_bucket = int(ts_sec // 3600)
        if hour_bucket != self._last_heartbeat_hour and (ts_sec % 3600) < 10.0:
            self._last_heartbeat_hour = hour_bucket
            fired.append("GLOBAL:HEARTBEAT_1H")

        if not fired:
            return emitted_before

        last_w = self._last_wake_by_sym.get(sym)
        is_global_hb = any("HEARTBEAT_1H" in r for r in fired)
        if (
            priority != "CRITICAL"
            and not is_global_hb
            and last_w is not None
            and (ts_sec - last_w) < self.cooldown_sec
        ):
            self.shadow_logger.log(
                should_wake=True,
                did_wake=False,
                reasons=[f"COOLDOWN_SUPPRESSED:{r}" for r in fired],
                symbols=[sym],
                metrics={"price": price, "hawkes": round(h_ratio, 2), "ts_sec": ts_sec},
            )
            return emitted_before

        self._last_wake_by_sym[sym] = ts_sec
        self._pending_symbols.add(sym)
        self._pending_reasons.extend(fired)
        if priority == "CRITICAL":
            self._pending_priority = "CRITICAL"
        if self._pending_opened_ts is None:
            self._pending_opened_ts = ts_sec
        self._pending_metrics[sym] = {
            "price": price,
            "ts_sec": ts_sec,
            "spread_bps": spread_bps,
            "hawkes": round(h_ratio, 2),
            "cvd_z": round(cvd_zscore_5m, 2),
            "imb10": round(imbalance_top10, 3),
        }

        if not self.virtual_clock:
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._coalesce_and_flush())
        return emitted_before

    async def flush_pending(self) -> DecisionEventV1 | None:
        """Immediately emit any pending coalesced event (used on shutdown or virtual replay end)."""
        if not self._pending_symbols:
            return None
        return await self._emit_pending(ts_sec=self._pending_opened_ts)

    async def _coalesce_and_flush(self) -> None:
        await asyncio.sleep(self.coalesce_sec)
        if self._pending_symbols:
            await self._emit_pending(ts_sec=self._pending_opened_ts)

    async def _emit_pending(self, *, ts_sec: float | None = None) -> DecisionEventV1:
        ev_ts = (
            datetime.fromtimestamp(ts_sec, tz=timezone.utc)
            if ts_sec is not None and ts_sec > 0
            else datetime.now(timezone.utc)
        )
        ev = DecisionEventV1(
            ts_utc=ev_ts,
            symbols=sorted(self._pending_symbols),
            reasons=list(dict.fromkeys(self._pending_reasons)),
            priority=self._pending_priority,
            metrics_snapshot=dict(self._pending_metrics),
        )
        self._pending_symbols.clear()
        self._pending_reasons.clear()
        self._pending_priority = "NORMAL"
        self._pending_metrics.clear()
        self._pending_opened_ts = None

        did_wake = not self.shadow_mode
        self.shadow_logger.log(
            should_wake=True,
            did_wake=did_wake,
            reasons=ev.reasons,
            symbols=ev.symbols,
            metrics=ev.metrics_snapshot,
            ts_utc=ev.ts_utc.isoformat(),
        )
        if did_wake:
            await self.on_decision_event(ev)
        elif self.on_shadow_event is not None:
            await self.on_shadow_event(ev)
        return ev

    async def run_zmq_subscriber(
        self,
        *,
        endpoint: str = "tcp://127.0.0.1:5555",
        on_heartbeat_touch: Callable[[float], None] | None = None,
        zmq_context: zmq.asyncio.Context | None = None,
        poll_timeout_ms: int = 100,
    ) -> None:
        """Subscribe to MarketDataHub ZMQ PUB socket without creating a second WebSocket."""
        ctx = zmq_context or zmq.asyncio.Context.instance()
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.LINGER, 0)
        sock.connect(endpoint)
        for prefix in ZMQ_SUB_PREFIXES:
            sock.setsockopt(zmq.SUBSCRIBE, prefix)

        poller = zmq.asyncio.Poller()
        poller.register(sock, zmq.POLLIN)

        try:
            while not self._stop_event.is_set():
                events = dict(await poller.poll(timeout=poll_timeout_ms))
                if sock not in events:
                    continue
                frames = await sock.recv_multipart()
                if len(frames) < 2:
                    continue
                topic = frames[0].decode("utf-8", errors="ignore")
                try:
                    payload = decode_zmq_payload(frames[1])
                except Exception:
                    continue

                ts_ns = payload.get("ts_ns")
                ts_sec = (
                    (float(ts_ns) / 1e9)
                    if ts_ns is not None
                    else float(payload.get("timestamp") or 0.0)
                )
                if on_heartbeat_touch and ts_sec > 0:
                    on_heartbeat_touch(ts_sec)

                sym = str(payload.get("symbol") or "").replace("-", "").replace("/", "").upper()
                if not sym or sym == "GLOBAL":
                    continue

                mid = float(
                    payload.get("mid_price")
                    or payload.get("mid")
                    or payload.get("price")
                    or payload.get("close")
                    or 0.0
                )
                if mid <= 0.0:
                    continue
                spread = float(payload.get("spread") or 0.0)
                spread_bps = (
                    (spread / mid * 10_000.0)
                    if (mid > 0 and spread > 0)
                    else float(payload.get("spread_bps") or 0.0)
                )
                await self.ingest_tick(
                    symbol=sym,
                    price=mid,
                    ts_sec=ts_sec,
                    is_trade=topic.startswith("trade."),
                    spread_bps=spread_bps,
                    imbalance_top10=float(payload.get("imbalance_top10") or 0.0),
                )
        finally:
            if self._flush_task is not None and not self._flush_task.done():
                await self._flush_task
            await self.flush_pending()
            self.shadow_logger.flush()
            sock.close(linger=0)

    def stop(self) -> None:
        self._stop_event.set()


__all__ = [
    "DecisionEventV1",
    "EventDrivenWakeupGate",
    "HawkesTracker",
    "ShadowWakeupLogger",
    "SymmetricCusumFilter",
    "ZMQ_SUB_PREFIXES",
    "decode_zmq_payload",
]
