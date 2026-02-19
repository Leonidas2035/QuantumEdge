from bot.engine.decision_types import (Decision, DecisionAction,
                                       DecisionDirection)
from bot.risk.scalp_guards import ScalpGuard
from bot.trading.execution_mode import ScalpExecutionMode


class DummySignal:
    def __init__(self, p_up: float, p_down: float, edge: float):
        self.p_up = p_up
        self.p_down = p_down
        self.edge = edge


def test_scalp_gate_spread_block():
    guard = ScalpGuard(max_positions=1, max_trades=10, max_loss_pct=5.0)
    cfg = {
        "min_prob_up": 0.55,
        "min_edge": 0.0,
        "max_spread_bps": 1.0,
        "min_orderbook_depth_usd": 100.0,
    }
    mode = ScalpExecutionMode(cfg, guard, order_policy=None, logger=None)
    decision = Decision(
        action=DecisionAction.ENTER, direction=DecisionDirection.LONG, size=1.0
    )
    signal = DummySignal(p_up=0.8, p_down=0.2, edge=0.1)
    last_event = {"b": 99.0, "a": 101.0, "q": 1.0, "depth": 1000.0}
    gate = mode.evaluate_entry(
        decision, price=100.0, symbol="BTCUSDT", signal=signal, last_event=last_event
    )
    assert not gate["ok"]
    assert gate["reason"] == "SPREAD_TOO_WIDE"
