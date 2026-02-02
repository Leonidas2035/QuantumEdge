import time

from policy.policy_contract import Policy, POLICY_VERSION
from policy.policy_gate import policy_allows_entry


def test_policy_gate_blocks_entry_allows_exit():
    now = int(time.time())
    policy = Policy(
        version=POLICY_VERSION,
        ts=now,
        ttl_sec=30,
        allow_trading=False,
        mode="risk_off",
        size_multiplier=1.0,
        cooldown_sec=0,
        reason="test",
    )
    assert policy_allows_entry("enter", policy) is False
    assert policy_allows_entry("exit", policy) is True


def test_policy_gate_cooldown_blocks_entry():
    now = int(time.time())
    policy = Policy(
        version=POLICY_VERSION,
        ts=now,
        ttl_sec=30,
        allow_trading=True,
        mode="normal",
        size_multiplier=1.0,
        cooldown_sec=10,
        reason="test",
    )
    assert policy_allows_entry("enter", policy, now_ts=now + 1) is False
