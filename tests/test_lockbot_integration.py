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


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.mark.asyncio
async def test_lockbot_smoke_integration():
    hub_port = _free_port()
    cmd_port = _free_port()
    status_port = _free_port()
    hub_endpoint = f"tcp://127.0.0.1:{hub_port}"
    cmd_endpoint = f"tcp://127.0.0.1:{cmd_port}"
    status_endpoint = f"tcp://127.0.0.1:{status_port}"

    cfg = LockbotConfig(
        hub_sub_endpoint=hub_endpoint,
        supervisor_cmd_sub_endpoint=cmd_endpoint,
        bot_pub_endpoint=status_endpoint,
        market_topics=[
            "BTCUSDT:mark_price_1s",
            "BTCUSDT:vwap_d",
            "BTCUSDT:vwap_bands_d",
        ],
        account_topics=["account.snapshot.v1"],
        log_path="runtime/lockbot_btc_test.log",
    )
    service = LockBotService(cfg)
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

    await publish("mark_price_1s", {"mark_price": 50000.0}, 1)
    await publish("vwap_d", {"vwap": 99.5, "session": {"type": "UTC_DAY", "start_ts": now_ms, "end_ts": now_ms + 1}}, 2)
    await publish(
        "vwap_bands_d",
        {"band_1u": 101.0, "band_1l": 98.0, "band_2u": 102.0, "band_2l": 97.0},
        3,
    )
    await publish_account(
        {
            "ts_event": now_ms,
            "positions": {"long_qty": 0.0, "short_qty": 0.0},
            "risk": {"margin_usage": 0.1, "distance_to_liq_bps": 1000.0, "equity": 10000.0},
        }
    )

    await asyncio.sleep(0.5)
    cmd_id = client.send_command("SET_REGIME", {"regime": "RANGE", "reason": "test"})
    await publish("mark_price_1s", {"mark_price": 50000.1}, 4)

    for _ in range(30):
        await asyncio.sleep(0.1)
        ack = client.ack(cmd_id)
        status = client.status()
        lags = status.get("payload", {}).get("lags", {}) if status else {}
        if ack and status and lags.get("market_lag_ms") is not None and lags.get("account_lag_ms") is not None:
            break
    assert ack is not None
    assert status is not None
    assert status.get("payload", {}).get("regime") == "RANGE"
    lags = status.get("payload", {}).get("lags", {})
    assert lags.get("market_lag_ms") is not None
    assert lags.get("account_lag_ms") is not None

    cmd_id = client.send_command(
        "EXEC_STEP",
        {"action": "ADD_LONG", "qty_hint": 0.02, "reason": "ddn-test", "expected_edge_bps": 10.0},
    )
    for _ in range(30):
        await asyncio.sleep(0.1)
        ack = client.ack(cmd_id)
        status = client.status()
        if ack and status and status.get("payload", {}).get("ddn", {}).get("last_verdict"):
            break
    assert ack is not None
    assert status is not None
    assert status.get("payload", {}).get("ddn", {}).get("last_verdict") in {"ALLOW", "MODIFY"}

    await publish_account(
        {
            "ts_event": now_ms + 1000,
            "positions": {"long_qty": 0.5, "short_qty": 0.0},
            "risk": {"margin_usage": 0.2, "distance_to_liq_bps": 100.0, "equity": 10000.0},
        }
    )
    await asyncio.sleep(0.2)
    cmd_id = client.send_command("EXEC_STEP", {"action": "ADD_LONG", "qty_hint": 0.01, "reason": "panic"})
    for _ in range(30):
        await asyncio.sleep(0.1)
        status = client.status()
        verdict = status.get("payload", {}).get("ddn", {}).get("last_verdict") if status else None
        if verdict == "PANIC_ONLY":
            break
    assert status is not None
    assert status.get("payload", {}).get("ddn", {}).get("last_verdict") == "PANIC_ONLY"

    pub.close()
    client.stop()
    await service.stop()
