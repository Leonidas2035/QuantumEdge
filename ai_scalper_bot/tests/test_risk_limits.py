import numpy as np
import time

from bot.engine.decision_engine import DecisionEngine
from bot.ml.ensemble import EnsembleOutput
from bot.ml.signal_model.model import SignalOutput
from bot.ml.feature_schema import FEATURE_NAMES
from bot.trading.trade_stats import TradeStats
from bot.engine.decision_types import DecisionAction

def test_risk_limits_block_after_losses():
    engine = DecisionEngine()
    symbol = "BTCUSDT"
    stats = TradeStats()
    engine.trade_stats[symbol] = stats
    # record losses equal to max_losses
    for _ in range(engine.loss_cfg.get("max_losses", 3)):
        stats.record(-1.0, time.time(), symbol=symbol, side="SELL")
    features = np.zeros(len(FEATURE_NAMES))
    ensemble = EnsembleOutput(meta_edge=0.1, direction=1, components={1: SignalOutput(0.6, 0.4, 0.1, 1)})
    decision = engine.decide(symbol=symbol, ensemble=ensemble, features=features, position=0, approved=True, warmup_ready=True)
    assert decision.action == DecisionAction.NO_TRADE
