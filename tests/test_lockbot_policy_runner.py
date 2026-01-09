import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_DIR = ROOT / "SupervisorAgent"
if str(SUPERVISOR_DIR) not in sys.path:
    sys.path.insert(0, str(SUPERVISOR_DIR))

from supervisor.lockbot.models import PolicyRunnerConfig
from supervisor.lockbot.policy_runner import LockbotPolicyRunner


class DummyControlClient:
    def __init__(self) -> None:
        self.sent = []
        self._status = None
        self._counter = 0

    def set_status(self, payload: dict) -> None:
        self._status = payload

    def send_command(self, cmd: str, payload: dict) -> str:
        self._counter += 1
        cmd_id = f"cmd-{self._counter}"
        self.sent.append((cmd, payload, cmd_id))
        return cmd_id

    def status(self):
        return self._status

    def ack(self, cmd_id: str):
        return None


def _status_payload() -> dict:
    return {
        "schema": "lockbot_control.v1",
        "msg_type": "status",
        "bot_id": "LockBotBTC",
        "symbol": "BTCUSDT",
        "ts_event": int(time.time() * 1000),
        "seq": 1,
        "payload": {
            "mode": "LOCKED",
            "regime": "RANGE",
            "net_delta_est": 0.0,
            "positions": {"long_qty": 0.1, "short_qty": 0.1},
            "lags": {"market_lag_ms": 0, "account_lag_ms": 0},
            "ddn": {"last_verdict": "ALLOW", "last_reasons": []},
            "policy": {"last_cmd_type": None},
        },
    }


def test_policy_runner_sends_regime_and_target() -> None:
    cfg = PolicyRunnerConfig(enabled=True, execution_enabled=False, min_leg_qty=0.0)
    client = DummyControlClient()
    client.set_status(_status_payload())
    runner = LockbotPolicyRunner(cfg, client)
    now_ms = int(time.time() * 1000)
    runner._market_cache.mark_price = 100.0
    runner._market_cache.mark_ts = now_ms
    runner._market_cache.vwap = 100.0
    runner._market_cache.band_1u = 101.0
    runner._market_cache.band_1l = 99.0
    runner._market_cache.band_2u = 102.0
    runner._market_cache.band_2l = 98.0
    runner.run_once(now_ms=now_ms)
    assert client.sent
    assert client.sent[0][0] == "SET_REGIME"


def test_policy_runner_cooldown_blocks_exec() -> None:
    cfg = PolicyRunnerConfig(enabled=True, execution_enabled=True, min_leg_qty=0.0)
    client = DummyControlClient()
    payload = _status_payload()
    payload["payload"]["ddn"]["last_verdict"] = "REJECT"
    client.set_status(payload)
    runner = LockbotPolicyRunner(cfg, client)
    now_ms = int(time.time() * 1000)
    runner._market_cache.mark_price = 103.0
    runner._market_cache.mark_ts = now_ms
    runner._market_cache.vwap = 100.0
    runner._market_cache.band_1u = 101.0
    runner._market_cache.band_1l = 99.0
    runner._market_cache.band_2u = 102.0
    runner._market_cache.band_2l = 98.0
    runner.run_once(now_ms=now_ms)
    assert all(cmd != "EXEC_STEP" for cmd, _, _ in client.sent)
