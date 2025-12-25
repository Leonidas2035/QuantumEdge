import json
import time
from pathlib import Path

from bot.policy.policy_client import PolicyClient
from bot.policy.policy_contract import POLICY_VERSION


def _write_policy(path: Path, ts: int, ttl_sec: int = 30, allow_trading: bool = True, mode: str = "normal"):
    payload = {
        "version": POLICY_VERSION,
        "ts": ts,
        "ttl_sec": ttl_sec,
        "allow_trading": allow_trading,
        "mode": mode,
        "size_multiplier": 1.0,
        "cooldown_sec": 0,
        "reason": "OK",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_policy_client_valid_file(tmp_path: Path):
    policy_path = tmp_path / "policy.json"
    _write_policy(policy_path, ts=int(time.time()), ttl_sec=30, allow_trading=True)
    client = PolicyClient(source="file", file_path=policy_path, api_url="")
    policy = client.get_effective_policy()
    assert policy.allow_trading is True
    assert policy.mode == "normal"


def test_policy_client_expired_file_falls_back(tmp_path: Path):
    policy_path = tmp_path / "policy.json"
    _write_policy(policy_path, ts=int(time.time()) - 100, ttl_sec=1, allow_trading=True)
    client = PolicyClient(source="file", file_path=policy_path, api_url="")
    policy = client.get_effective_policy()
    assert policy.allow_trading is False
    assert policy.reason == "POLICY_MISSING_OR_EXPIRED"


def test_policy_client_malformed_file_falls_back(tmp_path: Path):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text("{bad-json", encoding="utf-8")
    client = PolicyClient(source="file", file_path=policy_path, api_url="")
    policy = client.get_effective_policy()
    assert policy.allow_trading is False
