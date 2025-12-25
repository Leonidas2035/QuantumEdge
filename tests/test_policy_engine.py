import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_ROOT = ROOT / "SupervisorAgent"
if str(SUPERVISOR_ROOT) not in sys.path:
    sys.path.insert(0, str(SUPERVISOR_ROOT))

from SupervisorAgent.policy.circuit_breaker import CircuitBreaker
from SupervisorAgent.policy.heuristics import HeuristicDecision, HeuristicThresholds, apply_heuristics
from SupervisorAgent.policy.policy_engine import PolicyEngine, PolicyEngineConfig, HysteresisConfig, PolicyHysteresis
from SupervisorAgent.policy.signals import Signals


def _base_signals(**overrides) -> Signals:
    data = {
        "bot_running": True,
        "restart_rate": None,
        "pnl_day": None,
        "drawdown_day": None,
        "loss_streak": None,
        "error_rate": None,
        "spread_bps": None,
        "volatility": None,
        "latency_ms": None,
        "risk_halted": False,
        "risk_halt_reason": None,
        "evidence": {},
    }
    data.update(overrides)
    return Signals(**data)


def test_heuristics_bot_unhealthy():
    decision = apply_heuristics(_base_signals(bot_running=False), HeuristicThresholds())
    assert decision.mode == "risk_off"
    assert decision.allow_trading is False
    assert decision.reason == "BOT_UNHEALTHY"


def test_hysteresis_enter_exit():
    hysteresis = PolicyHysteresis(HysteresisConfig(enter_cycles=2, exit_cycles=2), state_path=None)
    risk_off = HeuristicDecision(
        mode="risk_off",
        allow_trading=False,
        size_multiplier=0.0,
        cooldown_sec=0,
        spread_max_bps=None,
        max_daily_loss=None,
        reason="BOT_UNHEALTHY",
        evidence="",
    )
    first = hysteresis.apply(risk_off, immediate=False)
    assert first.reason == "HYSTERESIS_WAIT"
    assert first.mode == "normal"
    second = hysteresis.apply(risk_off, immediate=False)
    assert second.mode == "risk_off"

    normal = HeuristicDecision(
        mode="normal",
        allow_trading=True,
        size_multiplier=1.0,
        cooldown_sec=0,
        spread_max_bps=None,
        max_daily_loss=None,
        reason="OK",
        evidence="",
    )
    hold = hysteresis.apply(normal, immediate=False)
    assert hold.reason == "HYSTERESIS_HOLD"
    assert hold.mode == "risk_off"
    exit_decision = hysteresis.apply(normal, immediate=False)
    assert exit_decision.mode == "normal"


def test_circuit_breaker_opens():
    cb = CircuitBreaker(failure_threshold=2, window_sec=60, open_sec=10)
    cb.record_failure()
    assert cb.allow()
    cb.record_failure()
    assert cb.allow() is False
    state = cb.state()
    assert state["open"] is True


def test_llm_failure_falls_back(monkeypatch, tmp_path: Path):
    def fake_collect_signals(*_args, **_kwargs):
        return _base_signals(pnl_day=0.0)

    from SupervisorAgent.policy import policy_engine as policy_engine_module

    monkeypatch.setattr(policy_engine_module, "collect_signals", fake_collect_signals)

    class DummyPaths:
        runtime_dir = tmp_path

    class DummyProcessManager:
        def get_status_payload(self):
            return {"state": "RUNNING", "restarts": 0}

    class DummyRiskEngine:
        def get_state(self):
            class State:
                halted = False
                halt_reason = None
                realized_pnl_today = 0.0
                equity_start = None
                equity_now = None
                max_equity_intraday = None

            return State()

    config = PolicyEngineConfig(
        update_interval_sec=5,
        ttl_sec=30,
        cooldown_sec=0,
        thresholds=HeuristicThresholds(),
        hysteresis=HysteresisConfig(enter_cycles=1, exit_cycles=1),
        llm_enabled=True,
        llm_model="gpt-4.1-mini",
        llm_api_url="https://example.com",
        llm_api_key_env="OPENAI_API_KEY_SUPERVISOR",
        llm_timeout_sec=0.1,
        llm_temperature=0.1,
        cb_failures=1,
        cb_window_sec=60,
        cb_open_sec=60,
        policy_state_path=tmp_path / "policy_state.json",
    )
    engine = PolicyEngine(
        config,
        DummyPaths(),
        DummyProcessManager(),
        DummyRiskEngine(),
        logging.getLogger("test"),
    )

    def raise_error(*_args, **_kwargs):
        raise RuntimeError("llm down")

    engine.llm.suggest = raise_error
    policy = engine.evaluate()
    assert "LLM_UNAVAILABLE" in policy.reason
