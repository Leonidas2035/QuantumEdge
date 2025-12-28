from bot.risk.circuit_breakers import CircuitBreakerManager


def test_exchange_error_breaker_trips_and_clears():
    cfg = {
        "cooldown_sec": 5,
        "exchange_errors_max": 2,
        "exchange_errors_window_sec": 60,
    }
    mgr = CircuitBreakerManager(cfg)
    mgr.record_exchange_error(now=1000)
    assert not mgr.status(now=1000).active
    mgr.record_exchange_error(now=1001)
    status = mgr.status(now=1001)
    assert status.active
    assert status.reason == "CB_EXCHANGE_ERRORS"
    assert not mgr.status(now=1007).active
