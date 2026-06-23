"""
Backtest Runner.
Orchestrates the event-driven simulation.
"""

import logging
from typing import List
from datetime import datetime
import numpy as np

from quantum_edge_core.backtesting.loader import QuestDataLoader
from quantum_edge_core.backtesting.mock_exchange import MockExchange
from quantum_edge_core.backtesting.metrics import BacktestMetrics

from hermes.context.accumulator import MarketAccumulator
from hermes.context.features import FeatureEngine
from quantum_edge_core.strategies.scalper_v1.bot.engine.decision_engine import (
    DecisionEngine,
)
from quantum_edge_core.strategies.scalper_v1.bot.trading.order_manager import (
    OrderManager,
)
from hermes.domain.models import PolicyContract, TradingMode

# ML Mocking
from quantum_edge_core.strategies.scalper_v1.bot.ml.ensemble import EnsembleOutput
from quantum_edge_core.strategies.scalper_v1.bot.ml.signal_model.model import (
    SignalOutput,
)

logger = logging.getLogger(__name__)


class BacktestRunner:
    def __init__(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        db_host: str = "http://localhost:9000",
    ):
        self.symbol = symbol
        self.start_time = start_time
        self.end_time = end_time
        self.loader = QuestDataLoader(host=db_host)
        self.exchange = MockExchange()

        # Components
        self.accumulator = MarketAccumulator()
        self.decision_engine = DecisionEngine()
        self.order_manager = OrderManager()

        # Policy for Backtest
        self.policy = PolicyContract(
            mode=TradingMode.NORMAL,
            long_allowed=True,
            short_allowed=True,
            max_leverage=5.0,
            min_order_size=0.001,
            max_position_size=5000.0,
            risk_multiplier=1.0,
            volatility_scalar=1.0,
        )

        self.equity_curve: List[float] = []

        # Candle Aggregation State
        self.current_candle = None
        self.current_candle_start = 0.0
        self.candle_interval = 60.0  # Default 1m

    def _update_candle(self, price: float, volume: float, ts: float):
        """Aggregate trades into candles."""
        # Align ts to minute bucket
        # If simulation uses 1m data, fine. If ticks, we aggregate.
        bucket = int(ts // self.candle_interval) * self.candle_interval

        if self.current_candle is None:
            self.current_candle = {
                "t": bucket,
                "o": price,
                "h": price,
                "l": price,
                "c": price,
                "v": volume,
            }
            self.current_candle_start = bucket
        elif bucket > self.current_candle_start:
            # Close previous
            self.accumulator.add_candle(self.current_candle)

            # Start new
            self.current_candle = {
                "t": bucket,
                "o": price,
                "h": price,
                "l": price,
                "c": price,
                "v": volume,
            }
            self.current_candle_start = bucket
        else:
            # Update current
            c = self.current_candle
            c["h"] = max(c["h"], price)
            c["l"] = min(c["l"], price)
            c["c"] = price
            c["v"] += volume

    def _generate_mock_ml_signal(self, features: np.ndarray) -> EnsembleOutput:
        """
        Generates a dummy ML signal based on basic feature heuristics
        so DecisionEngine has something to work with.
        """
        # Feature mapping (assuming standard vector from feature_engine)
        # 0: log_ret
        # 7: vol
        # ...
        # This relies on knowing feature index.
        # For simplicity, let's use a random walk or simple trend follower
        # if keys are not available.
        # But wait, FeatureEngine output is Dict usually in python context,
        # but bot might expect Array.

        # Let's inspect FeatureEngine.
        # It has static methods returning Dicts.
        # But DecisionEngine expects `ensemble` and `features` (array).

        # We'll mock a simple signal:
        # If returns > 0 -> Bullish

        # Mock Signal
        p_up = 0.5
        p_down = 0.5
        edge = 0.0

        # Heuristic: Momentum
        # If we passed features array (which we should construct)
        if features is not None and len(features) > 0:
            # simple heuristic using first element (ret)
            ret = features[0]
            if ret > 0.0005:
                p_up = 0.7
                p_down = 0.3
                edge = 0.1
            elif ret < -0.0005:
                p_up = 0.3
                p_down = 0.7
                edge = -0.1

        direction = 1 if edge > 0 else (-1 if edge < 0 else 0)
        sig = SignalOutput(p_up=p_up, p_down=p_down, edge=edge, direction=direction)

        # Components for horizons
        comps = {1: sig, 5: sig, 15: sig}

        return EnsembleOutput(
            meta_edge=edge,
            direction=1 if edge > 0 else (-1 if edge < 0 else 0),
            components=comps,
        )

    def _construct_feature_vector(self) -> np.ndarray:
        # Map accumulator state to feature vector expected by ML/Decision
        # Simplified for backtest
        # [ret_1, vol_10, ...]
        # We need `calc_features` or similar from feature engine to return array
        # `FeatureEngine` in context returns Dict.
        # We will create a dummy vector from `candles`
        if len(self.accumulator.candles) < 2:
            return np.zeros(10)

        c = list(self.accumulator.candles)
        last = c[-1]
        prev = c[-2]
        ret = (last.close / prev.close) - 1

        return np.array([ret] + [0.0] * 9)

    def run(self):
        logger.info(f"Starting backtest for {self.symbol}...")

        # Load Data
        events = self.loader.load_data(self.symbol, self.start_time, self.end_time)

        count = 0
        for event in events:
            count += 1
            # Update Exchange Time
            ts = event["timestamp"]  # datetime
            # Exchange needs float timestamp usually
            ts_float = ts.timestamp()
            self.exchange.current_time = ts_float

            # Process Fills (Limit Orders)
            self.exchange.on_tick(event)

            # Update Accumulator
            # Event: {timestamp, price, quantity, side, type}
            # Accumulator expects standard msg format or we unpack

            # Check type
            etype = event.get("type")
            if etype == "liquidation":
                # Skip liquidations for strategy decision for now,
                # or add to accumulator
                self.accumulator.add_liquidation(
                    {
                        "s": self.symbol,
                        "S": event["side"],
                        "o": event["price"],  # original price?
                        "q": event["quantity"],
                        "ap": event["price"],
                        "X": "FILLED",
                    }
                )
            else:
                # Trade
                # Accumulator expects: {"p": price, "q": qty, "m": is_maker(bool)}
                # or similar
                self.accumulator.add_trade(
                    {
                        "p": float(event["price"]),
                        "q": float(event["quantity"]),
                        "T": int(ts_float * 1000),
                        "m": event.get("side") == "SELL",  # Approx maker detection?
                    }
                )

                # Update Candle
                self._update_candle(
                    float(event["price"]), float(event["quantity"]), ts_float
                )

            # Decisions usually happen on Candle Close or Throttle
            # For HFT backtest, maybe every N ticks or every 1s
            # Let's run decision every tick (slow) or every time a minute bar closes (simulated)

            # For now, simplistic: run logic if features are ready (e.g. enough candles)
            # Use lower threshold for testing if needed, or rely on enough data
            min_candles = 2
            if len(self.accumulator.candles) < min_candles:
                continue

            # Calc Features
            # Context Features (ATR etc)
            atr = FeatureEngine.calc_atr(self.accumulator)
            vol_scalar = FeatureEngine.calc_volatility_scalar(self.accumulator)

            # Update Policy with Scalar
            self.policy.volatility_scalar = vol_scalar

            # ML Signal
            feats = self._construct_feature_vector()
            ensemble = self._generate_mock_ml_signal(feats)

            # Decision
            decision = self.decision_engine.decide(
                symbol=self.symbol,
                ensemble=ensemble,
                features=feats,
                position=int(self.exchange.positions.get(self.symbol, 0)),
                approved=True,
                warmup_ready=True,
            )

            # Execute
            cmd = decision.action
            if cmd == "enter" or (
                cmd == "hold"
                and decision.direction != 0
                and self.exchange.positions.get(self.symbol, 0) == 0
            ):
                # Only enter if flat? Or re-enter?
                # DecisionEngine returns ENTER if we should enter.
                # Check current pos
                pos = self.exchange.positions.get(self.symbol, 0)
                if pos == 0:
                    side = "BUY" if decision.direction == 1 else "SELL"
                    # Sizing
                    price = float(event["price"])
                    equity = self.exchange.equity  # MockExchange update?
                    # MockExchange keeps balance, we can calc equity
                    equity = self.exchange.get_equity(price)

                    qty = self.order_manager.calculate_entry_size(
                        self.symbol, price, equity, self.policy
                    )

                    if qty > 0:
                        # Place Market for simplicity in backtest
                        # (MockExchange supports Limit if we want to simulate SmartExecution)
                        self.exchange.execute_order(
                            self.symbol, side, qty, "MARKET", price
                        )

            elif cmd == "exit":
                pos = self.exchange.positions.get(self.symbol, 0)
                if pos != 0:
                    side = "SELL" if pos > 0 else "BUY"
                    self.exchange.execute_order(
                        self.symbol, side, abs(pos), "MARKET", float(event["price"])
                    )

            # Track Equity
            eq = self.exchange.get_equity(float(event["price"]))
            self.equity_curve.append(eq)

        logger.info(f"Backtest processed {count} events.")

        # Metrics
        stats = BacktestMetrics.calculate_stats(
            self.exchange.trades,
            self.equity_curve,
            self.exchange.balance,  # Initial was 10000 in MockExchange default
        )
        return stats
