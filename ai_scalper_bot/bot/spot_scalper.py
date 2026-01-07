"""Spot-only hot-path scalper engine (microstructure-driven)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Dict, Optional

from bot.market_data.hub_source import HubMarketDataSource
from bot.ops.status_writer import BotStatusWriter
from bot.telemetry.event_writer import EventWriter
from bot.trading.executor import BinanceDemoExecutor


@dataclass(frozen=True)
class BookTop:
    bid_px: float
    bid_qty: float
    ask_px: float
    ask_qty: float
    ts_ms: int

    @property
    def mid(self) -> float:
        if self.bid_px <= 0 or self.ask_px <= 0:
            return 0.0
        return (self.bid_px + self.ask_px) / 2.0

    @property
    def spread(self) -> float:
        return max(self.ask_px - self.bid_px, 0.0)

    @property
    def spread_bps(self) -> float:
        mid = self.mid
        if mid <= 0:
            return 0.0
        return self.spread / mid * 10_000.0


@dataclass(frozen=True)
class TopFeatures:
    spread_bps: float
    volume_imbalance: float
    short_vol_bps: float
    trend_score: float
    exp_move_bps: float


@dataclass(frozen=True)
class Signal:
    side: int  # -1, 0, +1
    confidence: float


@dataclass
class OpenOrder:
    side: int
    price: float
    qty: float
    ts_ms: int
    client_order_id: str
    order_id: Optional[str] = None
    requotes: int = 0
    remaining_qty: Optional[float] = None


@dataclass(frozen=True)
class OrderIntent:
    action: str  # place | cancel | replace | hold
    side: int
    price: float
    qty: float
    reason: str
    client_order_id: str
    order_id: Optional[str] = None


class FeatureComputer:
    def __init__(self, *, vol_window: int = 30, ema_fast: int = 5, ema_slow: int = 20) -> None:
        self._returns: Deque[float] = deque(maxlen=max(vol_window, 2))
        self._last_mid: Optional[float] = None
        self._ema_fast: Optional[float] = None
        self._ema_slow: Optional[float] = None
        self._alpha_fast = 2 / (ema_fast + 1)
        self._alpha_slow = 2 / (ema_slow + 1)

    def update(self, book: BookTop) -> TopFeatures:
        mid = book.mid
        if mid > 0 and self._last_mid:
            ret = (mid - self._last_mid) / self._last_mid
            self._returns.append(ret)
        self._last_mid = mid

        if mid > 0:
            if self._ema_fast is None:
                self._ema_fast = mid
            else:
                self._ema_fast = self._alpha_fast * mid + (1 - self._alpha_fast) * self._ema_fast
            if self._ema_slow is None:
                self._ema_slow = mid
            else:
                self._ema_slow = self._alpha_slow * mid + (1 - self._alpha_slow) * self._ema_slow

        volume_imbalance = _volume_imbalance(book.bid_qty, book.ask_qty)
        short_vol = _std(self._returns)
        short_vol_bps = short_vol * 10_000.0
        trend_score = 0.0
        if mid > 0 and self._ema_fast is not None and self._ema_slow is not None:
            trend_score = (self._ema_fast - self._ema_slow) / mid
        exp_move_bps = max(short_vol_bps, 0.0) * (1 + abs(volume_imbalance))

        return TopFeatures(
            spread_bps=book.spread_bps,
            volume_imbalance=volume_imbalance,
            short_vol_bps=short_vol_bps,
            trend_score=trend_score,
            exp_move_bps=exp_move_bps,
        )


class RegimeDetector:
    def __init__(self, *, max_spread_bps: float, max_short_vol_bps: float, trend_threshold: float) -> None:
        self._max_spread_bps = float(max_spread_bps)
        self._max_short_vol_bps = float(max_short_vol_bps)
        self._trend_threshold = float(trend_threshold)

    def classify(self, features: TopFeatures) -> str:
        if features.spread_bps > self._max_spread_bps:
            return "NO_TRADE"
        if features.short_vol_bps > self._max_short_vol_bps:
            return "HIGH_VOL"
        if abs(features.trend_score) > self._trend_threshold:
            return "TREND"
        return "RANGE"


class SignalEngine:
    def __init__(self, *, imbalance_threshold: float) -> None:
        self._threshold = float(imbalance_threshold)

    def generate(self, features: TopFeatures, regime: str) -> Signal:
        if regime in {"NO_TRADE", "HIGH_VOL"}:
            return Signal(side=0, confidence=0.0)
        imb = features.volume_imbalance
        if abs(imb) < self._threshold:
            return Signal(side=0, confidence=0.0)
        side = 1 if imb > 0 else -1
        confidence = min(abs(imb) / max(self._threshold, 1e-6), 1.0)
        return Signal(side=side, confidence=confidence)


class ExecutionEngine:
    def __init__(
        self,
        *,
        fee_bps: float,
        slippage_bps: float,
        ttl_ms: int,
        max_requotes: int,
        tick_size: float,
        min_qty: float,
    ) -> None:
        self._fee_bps = float(fee_bps)
        self._slippage_bps = float(slippage_bps)
        self._ttl_ms = int(ttl_ms)
        self._max_requotes = int(max_requotes)
        self._tick_size = float(tick_size)
        self._min_qty = float(min_qty)
        self._open: Optional[OpenOrder] = None

    def edge_ok(self, features: TopFeatures) -> bool:
        cost = features.spread_bps + 2 * self._fee_bps + self._slippage_bps
        return features.exp_move_bps > cost

    def build_intent(
        self,
        book: BookTop,
        signal: Signal,
        features: TopFeatures,
        now_ms: int,
        target_qty: float,
    ) -> Optional[OrderIntent]:
        if self._open and signal.side == 0:
            return self._cancel_intent("signal_flat")
        if self._open and not self.edge_ok(features):
            return self._cancel_intent("edge_weak")
        if not self._open:
            if signal.side == 0 or not self.edge_ok(features):
                return None
            return self._place_intent(book, signal, now_ms, target_qty, reason="new_entry")

        desired_price = book.bid_px if signal.side > 0 else book.ask_px
        if signal.side != self._open.side:
            return self._cancel_intent("side_flip")
        if now_ms - self._open.ts_ms >= self._ttl_ms:
            return self._replace_or_cancel(book, signal, now_ms, target_qty, "ttl_expired")
        if _price_diff(desired_price, self._open.price, self._tick_size):
            return self._replace_or_cancel(book, signal, now_ms, target_qty, "reprice")
        return None

    def apply_intent(self, intent: OrderIntent, now_ms: int) -> None:
        if intent.action in {"place", "replace"}:
            requotes = 0
            if self._open:
                requotes = self._open.requotes + (1 if intent.action == "replace" else 0)
            self._open = OpenOrder(
                side=intent.side,
                price=intent.price,
                qty=intent.qty,
                ts_ms=now_ms,
                client_order_id=intent.client_order_id,
                order_id=intent.order_id,
                requotes=requotes,
                remaining_qty=intent.qty,
            )
        elif intent.action == "cancel":
            self._open = None

    def record_fill(self, filled_qty: float) -> None:
        if not self._open:
            return
        remaining = (self._open.remaining_qty or self._open.qty) - filled_qty
        self._open.remaining_qty = max(remaining, 0.0)
        if self._open.remaining_qty <= 0:
            self._open = None

    def _place_intent(self, book: BookTop, signal: Signal, now_ms: int, target_qty: float, reason: str) -> OrderIntent:
        price = book.bid_px if signal.side > 0 else book.ask_px
        qty = self._desired_qty(target_qty)
        if qty <= 0:
            return self._cancel_intent("qty_zero")
        return OrderIntent(
            action="place",
            side=signal.side,
            price=price,
            qty=qty,
            reason=reason,
            client_order_id=_client_order_id(now_ms),
        )

    def _replace_or_cancel(
        self,
        book: BookTop,
        signal: Signal,
        now_ms: int,
        target_qty: float,
        reason: str,
    ) -> OrderIntent:
        if not self._open:
            return self._place_intent(book, signal, now_ms, target_qty, reason)
        if self._open.requotes >= self._max_requotes:
            return self._cancel_intent("max_requotes")
        price = book.bid_px if signal.side > 0 else book.ask_px
        return OrderIntent(
            action="replace",
            side=signal.side,
            price=price,
            qty=self._open.remaining_qty or self._open.qty,
            reason=reason,
            client_order_id=self._open.client_order_id,
            order_id=self._open.order_id,
        )

    def _desired_qty(self, target_qty: float) -> float:
        if target_qty <= 0:
            return self._min_qty
        return max(self._min_qty, target_qty)

    def _cancel_intent(self, reason: str) -> OrderIntent:
        open_order = self._open
        return OrderIntent(
            action="cancel",
            side=open_order.side if open_order else 0,
            price=open_order.price if open_order else 0.0,
            qty=open_order.remaining_qty if open_order and open_order.remaining_qty else 0.0,
            reason=reason,
            client_order_id=open_order.client_order_id if open_order else "",
            order_id=open_order.order_id if open_order else None,
        )


class RiskManager:
    def __init__(
        self,
        *,
        risk_per_trade: float,
        daily_dd_limit: float,
        max_consecutive_errors: int,
        spread_kill_bps: float,
        equity_usd: float,
    ) -> None:
        self._risk_per_trade = float(risk_per_trade)
        self._daily_dd_limit = float(daily_dd_limit)
        self._max_errors = int(max_consecutive_errors)
        self._spread_kill_bps = float(spread_kill_bps)
        self._equity_usd = float(equity_usd)
        self._consecutive_errors = 0
        self._drawdown = 0.0
        self._kill = False

    def allow(self, spread_bps: float) -> bool:
        if self._kill:
            return False
        if spread_bps > self._spread_kill_bps:
            self._kill = True
            return False
        if self._equity_usd > 0 and self._drawdown / self._equity_usd > self._daily_dd_limit:
            self._kill = True
            return False
        if self._consecutive_errors >= self._max_errors:
            self._kill = True
            return False
        return True

    def record_error(self) -> None:
        self._consecutive_errors += 1

    def record_success(self) -> None:
        self._consecutive_errors = 0

    def record_pnl(self, pnl: float) -> None:
        if pnl < 0:
            self._drawdown += abs(pnl)

    def risk_budget_qty(self, mid: float) -> float:
        if mid <= 0 or self._equity_usd <= 0:
            return 0.0
        notional = self._equity_usd * self._risk_per_trade
        return notional / mid

    @property
    def kill_switch(self) -> bool:
        return self._kill


class SpotScalperEngine:
    def __init__(
        self,
        *,
        feature_computer: FeatureComputer,
        regime_detector: RegimeDetector,
        signal_engine: SignalEngine,
        execution_engine: ExecutionEngine,
        risk_manager: RiskManager,
    ) -> None:
        self._features = feature_computer
        self._regime = regime_detector
        self._signal = signal_engine
        self._exec = execution_engine
        self._risk = risk_manager

    def on_book(self, book: BookTop, now_ms: int) -> Dict[str, object]:
        features = self._features.update(book)
        regime = self._regime.classify(features)
        signal = self._signal.generate(features, regime)
        allow = self._risk.allow(features.spread_bps)
        target_qty = self._risk.risk_budget_qty(book.mid)
        intent = None
        if allow and regime != "NO_TRADE" and regime != "HIGH_VOL":
            intent = self._exec.build_intent(book, signal, features, now_ms, target_qty)
        else:
            intent = self._exec.build_intent(
                book,
                Signal(side=0, confidence=0.0),
                features,
                now_ms,
                target_qty,
            )
        payload = {
            "ts_ms": now_ms,
            "bid_px": book.bid_px,
            "ask_px": book.ask_px,
            "features": {
                "spread_bps": features.spread_bps,
                "volume_imbalance": features.volume_imbalance,
                "short_vol_bps": features.short_vol_bps,
                "trend_score": features.trend_score,
                "exp_move_bps": features.exp_move_bps,
            },
            "regime": regime,
            "signal": {"side": signal.side, "confidence": signal.confidence},
            "allow": allow,
            "target_qty": target_qty,
            "intent": intent,
        }
        return payload

    def apply_intent(self, intent: OrderIntent, now_ms: int) -> None:
        self._exec.apply_intent(intent, now_ms)


class SpotOrderExecutor:
    def __init__(self, mode: str, logger: logging.Logger, *, enable_trading: bool) -> None:
        self._mode = mode if enable_trading else "paper"
        self._logger = logger
        self._client = BinanceDemoExecutor(exchange_override="spot") if self._mode == "demo" else None

    async def place_limit(self, symbol: str, side: str, qty: float, price: float, client_order_id: str) -> Optional[str]:
        if self._mode != "demo":
            return None
        result = await self._client.submit_order(
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            client_order_id=client_order_id,
            order_type="LIMIT",
            time_in_force="GTC",
            post_only=False,
        )
        if isinstance(result, dict):
            return str(result.get("orderId") or "") or None
        return None

    async def cancel(self, symbol: str, order_id: Optional[str], client_order_id: Optional[str]) -> bool:
        if self._mode != "demo":
            return True
        return await self._client.cancel_order(symbol, order_id=order_id, client_order_id=client_order_id)


def _cfg_section(config: object, key: str) -> Dict[str, object]:
    try:
        value = config.get(key, {})
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _cfg_value(config: object, key: str, default: object = None) -> object:
    try:
        return config.get(key, default)
    except Exception:
        return default


def _detect_futures_flags(config: object) -> list[str]:
    flags: list[str] = []
    app_cfg = _cfg_section(config, "app")
    enabled_market = str(_cfg_value(config, "enabled_market", app_cfg.get("enabled_market", "spot"))).lower()
    if enabled_market and enabled_market != "spot":
        flags.append(f"enabled_market={enabled_market}")
    if bool(_cfg_value(config, "futures_enabled", False)) or bool(app_cfg.get("use_futures", False)):
        flags.append("futures_enabled")
    demo_cfg = _cfg_section(config, "binance_demo")
    demo_exchange = str(demo_cfg.get("exchange", "")).lower()
    if demo_exchange in {"futures", "usdm", "usd-m", "perp"}:
        flags.append("binance_demo.exchange")
    base_url = str(demo_cfg.get("base_url", "")).lower()
    if "fapi" in base_url or "future" in base_url:
        flags.append("binance_demo.base_url")
    return flags


def _filter_spot_topics(raw_topics: object, symbols: list[str]) -> list[str]:
    allowed_types = {"l1", "depth_l2"}
    topics: list[str] = []
    if isinstance(raw_topics, list):
        for topic in raw_topics:
            text = str(topic)
            if ":" not in text:
                continue
            symbol, event_type = text.split(":", 1)
            if symbol in symbols and event_type in allowed_types:
                topics.append(text)
    if not topics:
        topics = [f"{symbol}:l1" for symbol in symbols] + [f"{symbol}:depth_l2" for symbol in symbols]
    return topics


def _resolve_path(config: object, raw_path: str) -> Path:
    base = Path(getattr(config, "qe_root", Path.cwd()))
    path = Path(raw_path)
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


async def run_spot_scalper(config: Dict[str, object], *, logger: Optional[logging.Logger] = None) -> None:
    logger = logger or logging.getLogger("spot_scalper")
    app_cfg = _cfg_section(config, "app")
    log_level = str(app_cfg.get("log_level", config.get("log_level", "INFO"))).upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
    mode = str(app_cfg.get("mode", config.get("mode", "paper"))).lower()
    futures_flags = _detect_futures_flags(config)
    trading_enabled = not futures_flags
    if futures_flags:
        logger.warning("Futures flags detected for scalper; forcing spot-only no-op (%s).", ", ".join(futures_flags))
        mode = "paper"

    data_cfg = _cfg_section(config, "market_data")
    binance_cfg = _cfg_section(config, "binance")
    demo_cfg = _cfg_section(config, "binance_demo")
    scalper_cfg = _cfg_section(config, "spot_scalper")
    symbols = list(
        scalper_cfg.get("symbols")
        or data_cfg.get("symbols")
        or binance_cfg.get("symbols")
        or demo_cfg.get("symbols")
        or ["BTCUSDT"]
    )
    if len(symbols) > 1:
        logger.warning("Spot scalper supports one symbol per instance; using %s only.", symbols[0])
        symbols = symbols[:1]
    hub_cfg = dict(data_cfg.get("hub", {}) or {})
    hub_cfg["topics"] = _filter_spot_topics(hub_cfg.get("topics"), symbols)
    hub_source = HubMarketDataSource(symbols, {"hub": hub_cfg})

    thresholds = scalper_cfg.get("thresholds", {}) or {}
    exec_cfg = scalper_cfg.get("execution", {}) or {}
    risk_cfg = scalper_cfg.get("risk", {}) or {}
    fees_cfg = scalper_cfg.get("fees", {}) or {}

    feature_computer = FeatureComputer(
        vol_window=int(thresholds.get("vol_window", 30)),
        ema_fast=int(thresholds.get("ema_fast", 5)),
        ema_slow=int(thresholds.get("ema_slow", 20)),
    )
    regime_detector = RegimeDetector(
        max_spread_bps=float(thresholds.get("max_spread_bps", 2.0)),
        max_short_vol_bps=float(thresholds.get("max_short_vol_bps", 15.0)),
        trend_threshold=float(thresholds.get("trend_threshold", 0.0005)),
    )
    signal_engine = SignalEngine(imbalance_threshold=float(thresholds.get("imbalance_threshold", 0.1)))
    execution_engine = ExecutionEngine(
        fee_bps=float(fees_cfg.get("fee_bps", 5.0)),
        slippage_bps=float(fees_cfg.get("slippage_bps", 1.0)),
        ttl_ms=int(exec_cfg.get("ttl_ms", 800)),
        max_requotes=int(exec_cfg.get("max_requotes", 3)),
        tick_size=float(exec_cfg.get("tick_size", 0.01)),
        min_qty=float(exec_cfg.get("min_qty", 0.001)),
    )
    risk_manager = RiskManager(
        risk_per_trade=float(risk_cfg.get("risk_per_trade", 0.01)),
        daily_dd_limit=float(risk_cfg.get("daily_dd_limit", 0.03)),
        max_consecutive_errors=int(risk_cfg.get("max_consecutive_errors", 5)),
        spread_kill_bps=float(risk_cfg.get("spread_kill_bps", 10.0)),
        equity_usd=float(risk_cfg.get("equity_usd", 0.0)),
    )

    engine = SpotScalperEngine(
        feature_computer=feature_computer,
        regime_detector=regime_detector,
        signal_engine=signal_engine,
        execution_engine=execution_engine,
        risk_manager=risk_manager,
    )

    events_path = _resolve_path(config, scalper_cfg.get("events_path", "ai_scalper_bot/runtime/events/spot_scalper.jsonl"))
    event_writer = EventWriter(events_path)
    status_file = _resolve_path(config, scalper_cfg.get("status_file", "ai_scalper_bot/runtime/status/spot_scalper.json"))
    status_writer = BotStatusWriter(status_file, interval_seconds=2.0)
    order_executor = SpotOrderExecutor(mode, logger, enable_trading=trading_enabled)

    await hub_source.start()
    logger.info("Spot scalper started (mode=%s trading=%s symbols=%s)", mode, trading_enabled, symbols)
    try:
        async for event in hub_source.stream():
            if str(event.get("event_type")) not in {"l1", "depth_l2"}:
                continue
            try:
                symbol = str(event.get("s") or symbols[0])
                if symbol != symbols[0]:
                    continue
                bid_px = float(event.get("b") or 0.0)
                ask_px = float(event.get("a") or 0.0)
                bid_qty = float(
                    event.get("bid_qty")
                    or event.get("best_bid_qty")
                    or event.get("quant_bid")
                    or 0.0
                )
                ask_qty = float(
                    event.get("ask_qty")
                    or event.get("best_ask_qty")
                    or event.get("quant_ask")
                    or 0.0
                )
                ts_ms = int(event.get("E") or event.get("T") or time.time() * 1000)
                if bid_px <= 0 or ask_px <= 0:
                    continue
                book = BookTop(bid_px=bid_px, bid_qty=bid_qty, ask_px=ask_px, ask_qty=ask_qty, ts_ms=ts_ms)
                payload = engine.on_book(book, ts_ms)
                intent = payload.get("intent")
                if isinstance(intent, OrderIntent):
                    await _apply_intent(intent, order_executor, event_writer, engine, ts_ms, symbol)
                event_writer.write({"type": "spot_scalper_tick", "data": _sanitize_payload(payload)})
                status_writer.update(
                    {
                        "ts": ts_ms,
                        "is_running": True,
                        "symbol": symbol,
                        "regime": payload.get("regime"),
                        "kill_switch": risk_manager.kill_switch,
                    }
                )
            except Exception as exc:
                logger.warning("Spot scalper loop error: %s", exc)
                risk_manager.record_error()
                event_writer.write({"type": "spot_scalper_error", "data": {"error": str(exc)}})
    finally:
        await hub_source.stop()


async def _apply_intent(
    intent: OrderIntent,
    executor: SpotOrderExecutor,
    event_writer: EventWriter,
    engine: SpotScalperEngine,
    ts_ms: int,
    symbol: str,
) -> None:
    action = intent.action
    if action == "place":
        side = "BUY" if intent.side > 0 else "SELL"
        order_id = await executor.place_limit(symbol, side, intent.qty, intent.price, intent.client_order_id)
        engine.apply_intent(intent, ts_ms)
        event_writer.write({"type": "spot_scalper_order", "data": {"action": "place", "order_id": order_id, **intent.__dict__}})
    elif action == "replace":
        await executor.cancel(symbol, intent.order_id, intent.client_order_id)
        side = "BUY" if intent.side > 0 else "SELL"
        order_id = await executor.place_limit(symbol, side, intent.qty, intent.price, intent.client_order_id)
        engine.apply_intent(intent, ts_ms)
        event_writer.write({"type": "spot_scalper_order", "data": {"action": "replace", "order_id": order_id, **intent.__dict__}})
    elif action == "cancel":
        await executor.cancel(symbol, intent.order_id, intent.client_order_id)
        engine.apply_intent(intent, ts_ms)
        event_writer.write({"type": "spot_scalper_order", "data": {"action": "cancel", **intent.__dict__}})


def _volume_imbalance(bid_qty: float, ask_qty: float) -> float:
    total = bid_qty + ask_qty
    if total <= 0:
        return 0.0
    return (bid_qty - ask_qty) / total


def _std(values: Deque[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var ** 0.5


def _price_diff(a: float, b: float, tick_size: float) -> bool:
    if tick_size <= 0:
        return a != b
    return abs(a - b) >= tick_size


def _client_order_id(ts_ms: int) -> str:
    return f"spot_scalp_{ts_ms}"


def _sanitize_payload(payload: Dict[str, object]) -> Dict[str, object]:
    data = dict(payload)
    if isinstance(data.get("intent"), OrderIntent):
        intent = data["intent"]
        data["intent"] = intent.__dict__
    return json.loads(json.dumps(data, default=str))
