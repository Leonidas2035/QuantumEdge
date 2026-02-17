"""
scripts/test_zmq_listener.py

Passive ZMQ Subscriber to verify MarketDataHub broadcasts.
Connects to tcp://127.0.0.1:5555 and decodes all events.
"""

import asyncio
import zmq
import zmq.asyncio
from quantum_edge_core.events import EventCodec


async def main():
    ctx = zmq.asyncio.Context()
    sock = ctx.socket(zmq.SUB)

    # CONNECT, do not bind (Hub binds)
    url = "tcp://127.0.0.1:5555"
    print(f"Connecting to {url}...")
    sock.connect(url)

    # Subscribe to all topics
    sock.setsockopt_string(zmq.SUBSCRIBE, "")

    print("Listening for events... (Ctrl+C to stop)")

    try:
        while True:
            # Receive multipart: [topic, payload]
            msg = await sock.recv_multipart()
            topic_bytes, payload = msg
            topic = topic_bytes.decode("utf-8")

            try:
                event = EventCodec.decode(payload)
                print(f"[RECEIVED] Topic: {topic} | Event: {event}")
            except Exception as e:
                print(f"[ERROR] Failed to decode event on topic {topic}: {e}")

    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        print("\nClosing socket...")
        sock.close()
        ctx.term()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
