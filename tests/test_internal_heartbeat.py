"""
Test script to simulate Bot Heartbeats using the fixed protocol.
This verifies if the Supervisor (once fixed) can receive heartbeats on ZMQ port 5557.
"""
import zmq
import time
import json
import logging
import sys
from datetime import datetime, timezone

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
            payload = {
                "uptime_s": 123.45,
                "pnl": 10.5,
                "active_positions": 1,
                "last_tick_ts": time.time(),
                "mode": "HEDGED",
                "details": {"fake": True},
                "equity": 10000.0,
                "realized_pnl_today": 5.0,
                "unrealized_pnl": 5.5,
                "open_positions_notional": 100.0,
                "base_currency": "USDT",
                "trading_day": datetime.now(timezone.utc).date().isoformat()
            }

            json_str = json.dumps(payload)

            # Send multipart message: [topic, payload]
            topic = b"heartbeat"
            socket.send_multipart([topic, json_str.encode("utf-8")])

            logger.info(f"Sent heartbeat: {payload['mode']} pnl={payload['pnl']}")
            time.sleep(1.0)

    except KeyboardInterrupt:
        logger.info("Stopping FakeBot...")
    finally:
        socket.close()
        context.term()

if __name__ == "__main__":
    run_fake_bot()
