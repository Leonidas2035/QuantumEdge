import time

import pytest

from bot.policy.policy_contract import Policy, POLICY_VERSION


def test_policy_contract_valid():
    now_ts = int(time.time())
    raw = {
        "version": POLICY_VERSION,
        "ts": now_ts,
        "ttl_sec": 30,
        "allow_trading": True,
        "mode": "normal",
        "size_multiplier": 1.0,
        "cooldown_sec": 0,
        "reason": "OK",
    }
    policy = Policy.from_dict(raw)
    assert policy.version == POLICY_VERSION
    assert policy.is_fresh(now_ts=now_ts)


def test_policy_contract_invalid():
    with pytest.raises(ValueError):
        Policy.from_dict({"version": POLICY_VERSION})
