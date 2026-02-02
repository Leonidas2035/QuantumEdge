from bot.risk.safety_gate import SafetyGate, DataFreshnessMonitor


def test_safety_gate_blocks_notional():
    gate = SafetyGate({"max_position_notional": 100.0})
    intent = {
        "action": "buy",
        "reduce_only": False,
        "notional": 150.0,
        "position_notional": 0.0,
    }
    decision = gate.evaluate(intent)
    assert not decision.allow
    assert decision.reason == "RISK_LIMIT_NOTIONAL"


def test_safety_gate_blocks_on_data_stale():
    gate = SafetyGate({})
    intent = {"action": "buy", "reduce_only": False, "notional": 10.0, "position_notional": 0.0}
    decision = gate.evaluate(intent, data_stale=True)
    assert not decision.allow
    assert decision.reason == "DATA_STALE"


def test_data_freshness_monitor():
    monitor = DataFreshnessMonitor(max_tick_ms=1000, max_book_ms=1000)
    monitor.update_tick(1000)
    assert not monitor.is_stale(1500)
    assert monitor.is_stale(2500)
