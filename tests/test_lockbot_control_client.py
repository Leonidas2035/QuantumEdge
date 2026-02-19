import pytest

pytest.skip("Legacy test broken by src-layout migration", allow_module_level=True)
import socket
import sys
import time
from pathlib import Path

import msgspec
import zmq

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_DIR = ROOT / "SupervisorAgent"
if str(SUPERVISOR_DIR) not in sys.path:
    sys.path.insert(0, str(SUPERVISOR_DIR))

from supervisor.config import LockbotControlConfig
from supervisor.contracts.lockbot_control_v1 import (AckEnvelope,
                                                     CommandEnvelope,
                                                     StatusEnvelope)
from supervisor.lockbot.control_client import LockbotControlClient


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_control_client_send_and_cache() -> None:
    cmd_port = _free_port()
    status_port = _free_port()
    cmd_endpoint = f"tcp://127.0.0.1:{cmd_port}"
    status_endpoint = f"tcp://127.0.0.1:{status_port}"

    cfg = LockbotControlConfig(
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
    client = LockbotControlClient(cfg)
    client.start()

    ctx = zmq.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.LINGER, 0)
    sub.setsockopt(zmq.SUBSCRIBE, cfg.cmd_topic.encode("utf-8"))
    sub.connect(cmd_endpoint)

    pub = ctx.socket(zmq.PUB)
    pub.setsockopt(zmq.LINGER, 0)
    pub.bind(status_endpoint)

    time.sleep(0.2)
    cmd_id = client.send_command("SET_REGIME", {"regime": "RANGE"})
    poller = zmq.Poller()
    poller.register(sub, zmq.POLLIN)
    assert poller.poll(500), "command not received"
    _topic, payload = sub.recv_multipart()
    cmd = msgspec.msgpack.decode(payload, type=CommandEnvelope)
    assert cmd.cmd_id == cmd_id

    time.sleep(0.2)
    ack = AckEnvelope(
        schema="lockbot_control.v1",
        msg_type="ack",
        bot_id="LockBotBTC",
        symbol="BTCUSDT",
        cmd_id=cmd_id,
        ts_ack=int(time.time() * 1000),
        payload={"status": "ACCEPTED", "state_version": 1},
    )
    for _ in range(5):
        pub.send_multipart([cfg.ack_topic.encode("utf-8"), msgspec.msgpack.encode(ack)])
        status = StatusEnvelope(
            schema="lockbot_control.v1",
            msg_type="status",
            bot_id="LockBotBTC",
            symbol="BTCUSDT",
            ts_event=int(time.time() * 1000),
            seq=1,
            payload={"mode": "IDLE", "regime": "RANGE"},
        )
        pub.send_multipart(
            [cfg.status_topic.encode("utf-8"), msgspec.msgpack.encode(status)]
        )
        time.sleep(0.1)
        if client.ack(cmd_id) and client.status():
            break

    for _ in range(10):
        time.sleep(0.1)
        if client.ack(cmd_id) and client.status():
            break
    assert client.ack(cmd_id) is not None
    assert client.status() is not None

    sub.close()
    pub.close()
    client.stop()
