import asyncio
import signal

import zmq
import zmq.asyncio

from quantum_edge_core.events import EventCodec
from quantum_edge_core.logging_setup import setup_logging
from quantum_edge_core.market_data.hub import MarketDataHubService


async def zmq_subscriber(stop_event):
    """Listens for ANY data on port 5555."""
    ctx = zmq.asyncio.Context()
    sub = ctx.socket(zmq.SUB)
    sub.connect("tcp://127.0.0.1:5555")
    sub.subscribe(b"")  # All topics (empty prefix)

    print("ZMQ Subscriber collecting events...")
    count = 0
    while not stop_event.is_set():
        try:
            # Poll with timeout to allow checking stop_event
            if await sub.poll(timeout=1000):
                topic_bytes, payload = await sub.recv_multipart()
                topic = topic_bytes.decode("utf-8")
                # We expect MockFeed to just publish events.
                # Note: MockFeed uses bus.publish which triggers hub._dispatcher_loop -> publisher.publish
                # hub.py publisher uses EventCodec

                try:
                    event = EventCodec.decode(payload)
                    count += 1
                    if count % 10 == 0:
                        print(f"Received {count} events. Last: {topic} -> {event}")
                except Exception as e:
                    print(f"Failed to decode: {e}")
            else:
                continue
        except Exception as e:
            print(f"ZMQ Error: {e}")
            break

    sub.close()
    ctx.term()


async def run_hub_test():
    setup_logging()

    # 1. Start Hub
    hub = MarketDataHubService()
    hub_task = asyncio.create_task(hub._runner_wrapper())

    print("Hub started (Mock Mode expected).")

    # 2. Start ZMQ Listener
    stop_sub = asyncio.Event()
    sub_task = asyncio.create_task(zmq_subscriber(stop_sub))

    # 3. Run for 5 seconds
    await asyncio.sleep(5)

    # 4. Shutdown
    print("Stopping Hub...")
    hub._handle_signal(signal.SIGINT)

    stop_sub.set()
    await sub_task

    try:
        await asyncio.wait_for(hub_task, timeout=5.0)
        print("[PASS] Hub stopped gracefully")
    except asyncio.TimeoutError:
        print("[FAIL] Hub stop timed out")


if __name__ == "__main__":
    from quantum_edge_core.utils.async_runner import run_service

    run_service(run_hub_test())
