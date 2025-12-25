"""Backtest engine and deterministic execution simulator."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..replay.adapters import MarketEvent

from .metrics import compute_metrics


@dataclass
class TradeFill:
    ts: int
    action: str
    price: float
    size: float
    fee: float
    pnl: float


@dataclass
class EquityPoint:
    ts: int
    equity: float


@dataclass
class BacktestConfig:
    symbol: str
    seed: int = 42
    fee_bps: float = 2.0
    slippage_bps: float = 0.5
    base_latency_ms: int = 0
    size: float = 1.0
    policy_mode: str = "normal"
    disable_policy: bool = False
    models_dir: Optional[Path] = None
    ml_mode: str = "auto"  # auto|runtime|disabled|simple


@dataclass
class BacktestResult:
    symbol: str
    config: BacktestConfig
    started_at: float
    finished_at: float
    trades: List[TradeFill]
    equity_curve: List[EquityPoint]
    metrics: dict

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "started_at": int(self.started_at),
            "finished_at": int(self.finished_at),
            "config": {
                "seed": self.config.seed,
                "fee_bps": self.config.fee_bps,
                "slippage_bps": self.config.slippage_bps,
                "base_latency_ms": self.config.base_latency_ms,
                "size": self.config.size,
                "policy_mode": self.config.policy_mode,
                "disable_policy": self.config.disable_policy,
                "models_dir": str(self.config.models_dir) if self.config.models_dir else None,
                "ml_mode": self.config.ml_mode,
            },
            "metrics": self.metrics,
            "trades": [
                {
                    "ts": t.ts,
                    "action": t.action,
                    "price": t.price,
                    "size": t.size,
                    "fee": t.fee,
                    "pnl": t.pnl,
                }
                for t in self.trades
            ],
        }


class ExecutionSimulator:
    def __init__(self, fee_bps: float, slippage_bps: float, base_latency_ms: int) -> None:
        self.fee_rate = max(fee_bps, 0.0) / 10_000
        self.slippage_rate = max(slippage_bps, 0.0) / 10_000
        self.base_latency_ms = max(base_latency_ms, 0)
        self.position = 0.0
        self.entry_price: Optional[float] = None
        self.realized_pnl = 0.0
        self.trades: List[TradeFill] = []

    def _fee(self, notional: float) -> float:
        return abs(notional) * self.fee_rate

    def _fill_price(self, mid: float, side: str) -> float:
        if side == "buy":
            return mid * (1 + self.slippage_rate)
        return mid * (1 - self.slippage_rate)

    def _record_trade(self, ts: int, action: str, price: float, size: float, pnl: float, fee: float) -> None:
        self.trades.append(TradeFill(ts=ts, action=action, price=price, size=size, fee=fee, pnl=pnl))

    def process(self, decision, event: MarketEvent, size_multiplier: float, allow_entry: bool) -> None:
        if decision is None:
            return
        action = getattr(decision, "action", "")
        direction = getattr(decision, "direction", "none")
        size = getattr(decision, "size", 0.0) or 0.0
        if size <= 0:
            size = 1.0
        size *= size_multiplier

        if action in {"hold", "no_trade"}:
            return

        latency_ms = self.base_latency_ms + int(getattr(event, "latency_ms", 0))
        ts = event.ts + latency_ms
        mid = event.price if event.price > 0 else (event.bid + event.ask) / 2

        if action in {"enter", "buy", "sell"}:
            if not allow_entry:
                return
            if direction == "long" or action == "buy":
                fill = self._fill_price(mid, "buy")
                fee = self._fee(fill * size)
                self.position = size
                self.entry_price = fill
                self._record_trade(ts, "open_long", fill, size, 0.0, fee)
            elif direction == "short" or action == "sell":
                fill = self._fill_price(mid, "sell")
                fee = self._fee(fill * size)
                self.position = -size
                self.entry_price = fill
                self._record_trade(ts, "open_short", fill, size, 0.0, fee)
            return

        if action in {"exit", "close"}:
            if not self.position or self.entry_price is None:
                return
            if self.position > 0:
                fill = self._fill_price(mid, "sell")
                fee = self._fee(fill * abs(self.position))
                pnl = (fill - self.entry_price) * abs(self.position) - fee
                self.realized_pnl += pnl
                self._record_trade(ts, "close_long", fill, abs(self.position), pnl, fee)
            else:
                fill = self._fill_price(mid, "buy")
                fee = self._fee(fill * abs(self.position))
                pnl = (self.entry_price - fill) * abs(self.position) - fee
                self.realized_pnl += pnl
                self._record_trade(ts, "close_short", fill, abs(self.position), pnl, fee)
            self.position = 0.0
            self.entry_price = None

    def mark_to_market(self, price: float) -> float:
        open_pnl = 0.0
        if self.position and self.entry_price:
            open_pnl = (price - self.entry_price) * self.position
        return self.realized_pnl + open_pnl


@dataclass
class _SimpleDecision:
    action: str
    direction: str = "none"
    size: float = 1.0


class SimpleStrategy:
    def __init__(self, threshold: float = 0.0) -> None:
        self.threshold = threshold
        self._last_price: Optional[float] = None

    def decide(self, event: MarketEvent, position: float):
        if self._last_price is None:
            self._last_price = event.price
            return None
        delta = event.price - self._last_price
        self._last_price = event.price

        if position == 0:
            if delta > self.threshold:
                return _SimpleDecision(action="enter", direction="long", size=1.0)
            if delta < -self.threshold:
                return _SimpleDecision(action="enter", direction="short", size=1.0)
            return None
        if position > 0 and delta < -self.threshold:
            return _SimpleDecision(action="exit", direction="short", size=1.0)
        if position < 0 and delta > self.threshold:
            return _SimpleDecision(action="exit", direction="long", size=1.0)
        return None


@dataclass
class _SimpleSignalOutput:
    p_up: float
    p_down: float
    edge: float
    direction: int


@dataclass
class _SimpleEnsembleOutput:
    meta_edge: float
    direction: int
    components: dict


class SimpleSignalProvider:
    def __init__(self, threshold: float = 0.0) -> None:
        self.threshold = threshold
        self._last_price: Optional[float] = None

    def predict(self, price: float):
        if self._last_price is None:
            self._last_price = price
            return None
        delta = price - self._last_price
        self._last_price = price
        edge = 0.0
        if delta > self.threshold:
            edge = 0.05
        elif delta < -self.threshold:
            edge = -0.05
        p_up = 0.5 + edge
        p_down = 0.5 - edge
        direction = 1 if edge > 0 else (-1 if edge < 0 else 0)
        signal = _SimpleSignalOutput(p_up=p_up, p_down=p_down, edge=edge, direction=direction)
        return _SimpleEnsembleOutput(meta_edge=edge, direction=direction, components={1: signal})


class BotStrategy:
    def __init__(self, symbol: str, models_dir: Optional[Path], ml_mode: str) -> None:
        from bot.engine.decision_engine import DecisionEngine
        from bot.ml.signal_model.online_features import OnlineFeatureBuilder

        self.symbol = symbol
        self.engine = DecisionEngine()
        self.features = OnlineFeatureBuilder(warmup_seconds=0)
        self.simple_provider = SimpleSignalProvider()
        self.ensemble = None

        if ml_mode == "disabled":
            return
        if ml_mode not in {"auto", "runtime"}:
            return
        try:
            from bot.ml.ensemble import EnsembleSignalModel
            from bot.ml.runtime_models import load_runtime_models, resolve_models_root
        except Exception:
            return

        if models_dir is None:
            models_dir = resolve_models_root()
        runtime_models = None
        thresholds = None
        try:
            loaded, errors = load_runtime_models(symbol, [1, 5, 30], models_root=models_dir)
            if loaded:
                runtime_models = {h: info.model for h, info in loaded.items()}
                thresholds = {h: info.threshold for h, info in loaded.items()}
        except Exception:
            runtime_models = None
        try:
            self.ensemble = EnsembleSignalModel(symbol=symbol, horizons=[1, 5, 30], runtime_models=runtime_models, thresholds=thresholds)
        except Exception:
            self.ensemble = None

    def decide(self, event: MarketEvent, position: float):
        features = self.features.add_tick(event.ts, event.price, event.qty, side=event.side)
        if features is None:
            return None
        if self.ensemble is None:
            ensemble = self.simple_provider.predict(event.price)
        else:
            try:
                ensemble = self.ensemble.predict(features)
            except Exception:
                ensemble = self.simple_provider.predict(event.price)
        if ensemble is None or not getattr(ensemble, "components", {}):
            return None
        decision = self.engine.decide(
            symbol=self.symbol,
            ensemble=ensemble,
            features=features,
            position=int(position),
            approved=True,
            warmup_ready=True,
        )
        return decision


class BacktestEngine:
    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.simulator = ExecutionSimulator(
            fee_bps=config.fee_bps,
            slippage_bps=config.slippage_bps,
            base_latency_ms=config.base_latency_ms,
        )
        self.rng = random.Random(config.seed)
        self.strategy = self._build_strategy()
        self.equity_curve: List[EquityPoint] = []

    def _build_strategy(self):
        if self.config.ml_mode == "simple":
            return SimpleStrategy()
        return BotStrategy(self.config.symbol, self.config.models_dir, self.config.ml_mode)

    def _policy_context(self) -> tuple[bool, float]:
        if self.config.disable_policy:
            return True, 1.0
        mode = (self.config.policy_mode or "normal").lower()
        if mode == "risk_off":
            return False, 0.0
        if mode == "conservative":
            return True, 0.5
        if mode == "scalp":
            return True, 0.8
        return True, 1.0

    def process_event(self, event: MarketEvent) -> None:
        allow_entry, size_multiplier = self._policy_context()
        decision = self.strategy.decide(event, self.simulator.position)
        self.simulator.process(decision, event, size_multiplier=size_multiplier, allow_entry=allow_entry)
        equity = self.simulator.mark_to_market(event.price)
        self.equity_curve.append(EquityPoint(ts=event.ts, equity=equity))

    def finalize(self, started_at: float, finished_at: float) -> BacktestResult:
        metrics = compute_metrics(self.simulator.trades, self.equity_curve)
        return BacktestResult(
            symbol=self.config.symbol,
            config=self.config,
            started_at=started_at,
            finished_at=finished_at,
            trades=self.simulator.trades,
            equity_curve=self.equity_curve,
            metrics=metrics,
        )

    def run(self, events: List[MarketEvent]) -> BacktestResult:
        started_at = time.time()
        for event in events:
            self.process_event(event)
        finished_at = time.time()
        return self.finalize(started_at, finished_at)
