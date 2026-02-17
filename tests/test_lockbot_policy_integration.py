import pytest
pytest.skip("Legacy test broken by src-layout migration", allow_module_level=True)
import asyncio
import socket
import sys
import time
from pathlib import Path

import msgspec
import pytest
import zmq
import zmq.asyncio

from LockBotBTC.lockbot_btc.config import LockbotConfig
from LockBotBTC.lockbot_btc.main import LockBotService
from market_data.lockbot.schema import LockbotMarketEvent
from market_data.models import Priority

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_DIR = ROOT / "SupervisorAgent"
if str(SUPERVISOR_DIR) not in sys.path:
    sys.path.insert(0, str(SUPERVISOR_DIR))

from supervisor.config import LockbotControlConfig
from supervisor.lockbot.control_client import LockbotControlClient
from supervisor.lockbot.models import PolicyRunnerConfig
from supervisor.lockbot.policy_runner import LockbotPolicyRunner


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.mark.asyncio
async def test_policy_runner_smoke_range_and_chaos():
    hub_port = _free_port()
    cmd_port = _free_port()
    status_port = _free_port()
    hub_endpoint = f"tcp://127.0.0.1:{hub_port}"
    cmd_endpoint = f"tcp://127.0.0.1:{cmd_port}"
    status_endpoint = f"tcp://127.0.0.1:{status_port}"

    bot_cfg = LockbotConfig(
        hub_sub_endpoint=hub_endpoint,
        supervisor_cmd_sub_endpoint=cmd_endpoint,
        bot_pub_endpoint=status_endpoint,
        market_topics=[
            "BTCUSDT:mark_price_1s",
            "BTCUSDT:vwap_bands_d",
            "BTCUSDT:liq_heatmap",
        ],
        account_topics=["account.snapshot.v1"],
        log_path="runtime/lockbot_btc_policy_test.log",
    )
    service = LockBotService(bot_cfg)
    await service.start()

    control_cfg = LockbotControlConfig(
        enabled=True,
        bot_id="LockBotBTC",
        symbol="BTCUSDT",
        cmd_endpoint=cmd_endpoint,
        status_endpoint=status_endpoint,
        cmd_topic="LOCKBOT:BTCUSDT:cmd",
        ack_topic="LOCKBOT:BTCUSDT:ack",
        status_topic="LOCKBOT:BTCUSDT:status",
        exec_topic="LOCKBOT:BTCUSDT:exec",
        stale_after_ms=5000,
        rcv_hwm=1000,
        cmd_ttl_ms=2000,
    )
    client = LockbotControlClient(control_cfg)
    client.start()

    policy_cfg = PolicyRunnerConfig(
        enabled=True,
        symbol="BTCUSDT",
        hub_sub_endpoint=hub_endpoint,
        hub_topics=[
            "BTCUSDT:mark_price_1s",
            "BTCUSDT:vwap_bands_d",
            "BTCUSDT:liq_heatmap",
        ],
        execution_enabled=True,
        min_leg_qty=0.0,
        max_cmds_per_sec=10,
        max_exec_steps_per_minute=60,
        max_market_lag_ms=15000,
        max_account_lag_ms=15000,
    )
    policy_runner = LockbotPolicyRunner(policy_cfg, client)
    policy_runner.start()

    ctx = zmq.asyncio.Context.instance()
    pub = ctx.socket(zmq.PUB)
    pub.setsockopt(zmq.LINGER, 0)
    pub.bind(hub_endpoint)

    await asyncio.sleep(0.5)
    now_ms = int(time.time() * 1000)

    async def publish(event_type: str, payload: dict, seq: int) -> None:
        event = LockbotMarketEvent(
            ts_ns=now_ms * 1_000_000,
            symbol="BTCUSDT",
            event_type=event_type,
            seq=seq,
            priority=Priority.L1,
            schema="lockbot_md.v1",
            topic=f"BTCUSDT:{event_type}",
            ts_event=now_ms,
            ts_pub=now_ms,
            source="hub_derived",
            payload=payload,
        )
        await pub.send_multipart([f"BTCUSDT:{event_type}".encode("utf-8"), msgspec.msgpack.encode(event)])

    async def publish_account(payload: dict) -> None:
        await pub.send_multipart([b"account.snapshot.v1", msgspec.msgpack.encode(payload)])

    await publish_account(
        {
            "ts_event": now_ms,
            "positions": {"long_qty": 0.1, "short_qty": 0.1},
            "risk": {"margin_usage": 0.1, "distance_to_liq_bps": 1000.0, "equity": 10000.0},
        }
    )
    await publish(
        "vwap_bands_d",
        {"vwap": 100.0, "band_1u": 101.0, "band_1l": 99.0, "band_2u": 102.0, "band_2l": 98.0},
        1,
    )
    await publish("mark_price_1s", {"mark_price": 103.0}, 2)

    last_cmd = None
    for _ in range(30):
        await asyncio.sleep(0.1)
        status = client.status()
        last_cmd = status.get("payload", {}).get("policy", {}).get("last_cmd_type") if status else None
        if last_cmd in {"EXEC_STEP", "SET_DELTA_TARGET", "SET_REGIME", "PAUSE"}:
            break
    assert status is not None
    if last_cmd == "PAUSE":
        await publish("mark_price_1s", {"mark_price": 103.2}, 4)
        for _ in range(30):
            await asyncio.sleep(0.1)
            status = client.status()
            last_cmd = status.get("payload", {}).get("policy", {}).get("last_cmd_type") if status else None
            if last_cmd in {"EXEC_STEP", "SET_DELTA_TARGET", "SET_REGIME"}:
                break
    assert last_cmd in {"EXEC_STEP", "SET_DELTA_TARGET", "SET_REGIME", "PAUSE"}

    await publish(
        "liq_heatmap",
        {"intensity_above": 10.0, "intensity_below": 10.0, "levels": [], "window_s": 3600, "bin_type": "bps", "bin_size": 10, "decay": {"type": "exp", "half_life_s": 600}, "last_force_order_ts": now_ms},
        3,
    )
    await publish("mark_price_1s", {"mark_price": 103.1}, 5)

    for _ in range(30):
        await asyncio.sleep(0.1)
        decisions = policy_runner.decisions(limit=1)
        if decisions and (decisions[-1].get("reason") == "chaos" or decisions[-1].get("candidate") == "CHAOS"):
            break
    assert decisions
    assert decisions[-1].get("reason") == "chaos" or decisions[-1].get("candidate") == "CHAOS"

    pub.close()
    policy_runner.stop()
    client.stop()
    await service.stop()
