#!/usr/bin/env python3
"""
Probe: MarketDataHub ZMQ PUB verification.

Subscribes to tcp://127.0.0.1:5555 and validates that the Hub is
broadcasting events via multipart [topic, payload].

The Hub's ZmqPublisher uses msgspec JSON encoding (EventCodec.encode),
but the bot's ZmqSubStream decodes with ujson — both produce valid
JSON bytes ⇒ we decode with json.loads for zero-dependency probing.

Usage:
    python3 scripts/probe_zmq_hub.py [--timeout 10] [--count 5]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

import zmq

# Strict: modular loggers only, no print()
logger = logging.getLogger(__name__)

HUB_ENDPOINT: str = "tcp://127.0.0.1:5555"


def probe_hub(timeout_s: int = 10, expected_count: int = 5) -> int:
    """
    Connect SUB socket to Hub and collect events.

    Returns:
        0 on success, 1 on failure.
    """
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVHWM, 500)
    sub.setsockopt_string(zmq.SUBSCRIBE, "")  # global subscription
    sub.connect(HUB_ENDPOINT)

    logger.info(
        "Connected to %s — waiting up to %ds for %d events...",
        HUB_ENDPOINT,
        timeout_s,
        expected_count,
    )

    received: int = 0
    deadline: float = time.monotonic() + timeout_s

    try:
        while received < expected_count and time.monotonic() < deadline:
            if sub.poll(timeout=1000) == 0:
                continue

            frames: list[bytes] = sub.recv_multipart(zmq.NOBLOCK)
            if len(frames) < 2:
                logger.warning("Received %d frames — expected ≥2", len(frames))
                continue

            topic: str = frames[0].decode("utf-8", errors="replace")
            try:
                payload: dict = json.loads(frames[1])
            except json.JSONDecodeError:
                logger.warning("Malformed JSON on topic='%s'", topic)
                continue

            received += 1
            ev_type: str = payload.get("type", payload.get("event_type", "?"))
            logger.info(
                "[%d/%d] topic=%-30s event_type=%-20s keys=%s",
                received,
                expected_count,
                topic,
                ev_type,
                list(payload.keys())[:8],
            )
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        sub.close()
        ctx.term()

    if received >= expected_count:
        logger.info("✅  Hub probe PASSED — %d events collected", received)
        return 0
    else:
        logger.error(
            "❌  Hub probe FAILED — only %d/%d events in %ds",
            received,
            expected_count,
            timeout_s,
        )
        return 1


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="MarketDataHub ZMQ probe")
    parser.add_argument("--timeout", type=int, default=10, help="Seconds to wait")
    parser.add_argument("--count", type=int, default=5, help="Min events to collect")
    args = parser.parse_args()
    sys.exit(probe_hub(args.timeout, args.count))


if __name__ == "__main__":
    main()
