from __future__ import annotations

from supervisor_llm.router.circuit import CircuitBreaker, CircuitConfig


def test_circuit_breaker_opens(tmp_path):
    config = CircuitConfig(failure_threshold=2, window_s=60, cool_down_s=120)
    breaker = CircuitBreaker("teacher", config, state_path=tmp_path / "state.json")

    breaker.record_failure(100.0)
    assert breaker.is_open(100.0) is False

    breaker.record_failure(101.0)
    assert breaker.is_open(101.0) is True
