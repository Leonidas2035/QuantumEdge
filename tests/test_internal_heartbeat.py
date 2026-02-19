"""
Test script to simulate Bot Heartbeats using the fixed protocol.
This verifies if the Supervisor (once fixed) can receive heartbeats on ZMQ port 5557.
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone

import zmq

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("FakeBot")


def run_fake_bot():
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    port = 5557
    endpoint = f"tcp://*:{port}"

    try:
        socket.bind(endpoint)
        logger.info(f"FakeBot bound to {endpoint}")
    except zmq.ZMQError as e:
        logger.error(f"Failed to bind to {endpoint}: {e}")
        sys.exit(1)

    logger.info("Sending heartbeats every 1s... Press Ctrl+C to stop.")

    try:
        while True:
            # Construct payload matching HeartbeatPayload in supervisor/heartbeat.py
            # AND matching the fix we plan to apply in reporter.py
            # New Schema
            payload = {
                "service_id": "ai_scalper_bot",
                "timestamp": time.time(),
                "state": "RUNNING",
                "metrics": {
                    "pnl_session": 10.5,
                    "active_positions_count": 1,
                    "current_drawdown_pct": 0.0,
                    "cpu_usage": 0.0,
                },
                "errors": [],
            }

            json_str = json.dumps(payload)

            # Send multipart message: [topic, payload]
            topic = b"heartbeat"
            socket.send_multipart([topic, json_str.encode("utf-8")])

            logger.info(
                f"Sent heartbeat: {payload['state']} pnl={payload['metrics']['pnl_session']}"
            )
            time.sleep(1.0)

    except KeyboardInterrupt:
        logger.info("Stopping FakeBot...")
    finally:
        socket.close()
        context.term()


if __name__ == "__main__":
    run_fake_bot()
