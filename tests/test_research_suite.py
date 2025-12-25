import hashlib
import json
from pathlib import Path

from SupervisorAgent.research.backtest.engine import BacktestConfig, BacktestEngine
from SupervisorAgent.research.replay.adapters import MarketEvent, load_events
from SupervisorAgent.research.scenarios.definitions import get_scenario
from SupervisorAgent.research.scenarios.injector import inject_scenario


def _result_hash(result) -> str:
    payload = {
        "metrics": result.metrics,
        "trades": [t.__dict__ for t in result.trades],
    }
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def test_backtest_deterministic():
    data_file = Path(__file__).parent / "data" / "ticks_small.csv"
    events = load_events(data_file)
    cfg = BacktestConfig(symbol="BTCUSDT", seed=42, ml_mode="simple", disable_policy=True)
    result1 = BacktestEngine(cfg).run(events)
    result2 = BacktestEngine(cfg).run(events)
    assert _result_hash(result1) == _result_hash(result2)


def test_scenario_injection():
    base = [
        MarketEvent(ts=1000 + i * 1000, price=100.0, bid=99.5, ask=100.5, qty=1.0, side="buy")
        for i in range(10)
    ]
    spread = inject_scenario(base, get_scenario("spread_spike"), seed=1)
    assert any((evt.ask - evt.bid) > 1.0 for evt in spread)

    latency = inject_scenario(base, get_scenario("latency_spike"), seed=1)
    assert any(evt.latency_ms > 0 for evt in latency)

    vol = inject_scenario(base, get_scenario("volatility_spike"), seed=1)
    assert any(evt.price != 100.0 for evt in vol)
