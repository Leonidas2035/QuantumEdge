from bot.engine.decision_types import DecisionDirection
from bot.ml.runtime.aggregator import MultiHorizonAggregator
from bot.ml.signal_model.model import SignalOutput


def _outputs():
    return {
        1: SignalOutput(p_up=0.7, p_down=0.3, edge=0.2, direction=1),
        5: SignalOutput(p_up=0.66, p_down=0.34, edge=0.16, direction=1),
        30: SignalOutput(p_up=0.62, p_down=0.38, edge=0.12, direction=1),
    }


def test_and_gate_passes():
    agg = MultiHorizonAggregator(policy="and_gate", thresholds={1: 0.6, 5: 0.6, 30: 0.6})
    gate = agg.evaluate(_outputs(), DecisionDirection.LONG, now_ms=1, last_trade_ms=None)
    assert gate.allow


def test_and_gate_blocks():
    agg = MultiHorizonAggregator(policy="and_gate", thresholds={1: 0.8, 5: 0.6, 30: 0.6})
    gate = agg.evaluate(_outputs(), DecisionDirection.LONG, now_ms=1, last_trade_ms=None)
    assert not gate.allow
    assert "ML_THRESHOLD_FAIL_H1" in gate.reasons


def test_weighted_gate():
    agg = MultiHorizonAggregator(
        policy="weighted",
        thresholds={1: 0.0, 5: 0.0, 30: 0.0},
        weights={1: 1.0, 5: 1.0, 30: 1.0},
        score_threshold=0.1,
    )
    gate = agg.evaluate(_outputs(), DecisionDirection.LONG, now_ms=1, last_trade_ms=None)
    assert gate.allow


def test_two_stage_blocks_on_h1():
    outputs = _outputs()
    outputs[1] = SignalOutput(p_up=0.4, p_down=0.6, edge=-0.1, direction=-1)
    agg = MultiHorizonAggregator(policy="two_stage", thresholds={1: 0.6, 5: 0.6, 30: 0.6})
    gate = agg.evaluate(outputs, DecisionDirection.LONG, now_ms=1, last_trade_ms=None)
    assert not gate.allow
    assert "ML_THRESHOLD_FAIL_H1" in gate.reasons
