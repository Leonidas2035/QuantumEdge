from hermes.supervisor.autopilot.remediation import RateLimiter


def test_rate_limiter_blocks_after_limit():
    limiter = RateLimiter(1)
    assert limiter.allow(now=1000)
    assert not limiter.allow(now=1001)
