#!/usr/bin/env python3
"""
Probe: AI Scalper Bot telemetry verification.

The Bot's SupervisorReporter binds PUB on tcp://*:5557 and sends
multipart messages:
  [b"telemetry",                  payload]  — heartbeat
  [b"telemetry.ai_scalper_bot",   payload]  — detailed telemetry

This probe subscribes to both topics and validates the JSON schema.

Usage:
    python3 scripts/probe_telemetry.py [--timeout 15]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

import zmq

logger = logging.getLogger(__name__)

TELEMETRY_ENDPOINT: str = "tcp://127.0.0.1:5557"
EXPECTED_HEARTBEAT_KEYS: frozenset[str] = frozenset(
    {
        "source",
        "timestamp",
        "status",
        "pnl_session",
    }
)
EXPECTED_TELEMETRY_KEYS: frozenset[str] = frozenset(
    {
        "service_id",
        "timestamp",
        "last_price",
        "ofi_1s",
        "active_signal",
    }
)


def probe_telemetry(timeout_s: int = 15) -> int:
    """
    Connect SUB to :5557 and validate heartbeat + telemetry payloads.

    Returns:
        0 on success (both heartbeat and telemetry received), 1 otherwise.
    """
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt_string(zmq.SUBSCRIBE, "telemetry")  # catches both topics
    sub.connect(TELEMETRY_ENDPOINT)

    logger.info(
        "Subscribed to %s — waiting %ds for heartbeat+telemetry...",
        TELEMETRY_ENDPOINT,
        timeout_s,
    )

    seen_heartbeat: bool = False
    seen_telemetry: bool = False
    deadline: float = time.monotonic() + timeout_s

    try:
        while time.monotonic() < deadline:
            if seen_heartbeat and seen_telemetry:
                break
            if sub.poll(timeout=1000) == 0:
                continue

            frames: list[bytes] = sub.recv_multipart(zmq.NOBLOCK)
            if len(frames) < 2:
                continue

            topic: str = frames[0].decode("utf-8", errors="replace")
            try:
                payload: dict = json.loads(frames[1])
            except json.JSONDecodeError:
                logger.warning("Malformed JSON on topic='%s'", topic)
                continue

            if topic == "telemetry" and not seen_heartbeat:
                missing = EXPECTED_HEARTBEAT_KEYS - payload.keys()
                if missing:
                    logger.warning("Heartbeat missing keys: %s", missing)
                else:
                    logger.info(
                        "✅  Heartbeat OK — source=%s status=%s pnl=%.4f",
                        payload.get("source"),
                        payload.get("status"),
                        payload.get("pnl_session", 0.0),
                    )
                    seen_heartbeat = True

            elif topic == "telemetry.ai_scalper_bot" and not seen_telemetry:
                missing = EXPECTED_TELEMETRY_KEYS - payload.keys()
                if missing:
                    logger.warning("Telemetry missing keys: %s", missing)
                else:
                    logger.info(
                        "✅  Telemetry OK — price=%.2f ofi=%.4f signal=%s",
                        payload.get("last_price", 0.0),
                        payload.get("ofi_1s", 0.0),
                        payload.get("active_signal", "?"),
                    )
                    seen_telemetry = True

    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        sub.close()
        ctx.term()

    if seen_heartbeat and seen_telemetry:
        logger.info("✅  Telemetry probe PASSED (heartbeat + telemetry)")
        return 0
    else:
        if not seen_heartbeat:
            logger.error("❌  No heartbeat received on 'telemetry' topic")
        if not seen_telemetry:
            logger.error(
                "❌  No telemetry received on 'telemetry.ai_scalper_bot' topic"
            )
        return 1


def probe_command_bus() -> None:
    """
    Check if Supervisor's Command PUB on :5558 is reachable (connect only).
    """
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt_string(zmq.SUBSCRIBE, "command.")
    try:
        sub.connect("tcp://127.0.0.1:5558")
        logger.info("✅  Command Bus :5558 — socket connected (PUB may not have data)")
    except zmq.ZMQError as exc:
        logger.error("❌  Command Bus :5558 — connect failed: %s", exc)
    finally:
        sub.close()
        ctx.term()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Bot telemetry probe")
    parser.add_argument("--timeout", type=int, default=15, help="Seconds to wait")
    args = parser.parse_args()

    probe_command_bus()
    sys.exit(probe_telemetry(args.timeout))


if __name__ == "__main__":
    main()
