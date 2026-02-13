import asyncio
import multiprocessing
import time
import zmq
import zmq.asyncio

from quantum_edge_core.market_data.hub import MarketDataHubService
from quantum_edge_core.utils.async_runner import run_service
from quantum_edge_core.events import EventCodec, LargeBlockEvent


# 1. Hub Process
def run_hub():
    # Run Hub in a separate process
    hub = MarketDataHubService()
    # MockFeed is default, configured to 1% whale probability
    run_service(hub._runner_wrapper())


# 2. Listener
async def listen_for_whale():
    ctx = zmq.asyncio.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect("tcp://127.0.0.1:5555")
    sock.subscribe("market.alpha.whale")

    print("Listening for WHALE alerts...")

    # Wait for up to 10 seconds (Mock feed runs 100hz, 1% chance -> expect 1 per sec avg)
    start = time.time()
    while time.time() - start < 10:
        try:
            msg = await asyncio.wait_for(sock.recv_multipart(), timeout=1.0)
            topic = msg[0].decode()
            payload = msg[1]

            event = EventCodec.decode(payload)
            if isinstance(event, LargeBlockEvent):
                print(f"[PASS] Whale Detected! {event.side} {event.quantity} BTC at {event.price}")
                return True

        except asyncio.TimeoutError:
            continue
        except Exception as e:
            print(f"Error: {e}")

    print("[FAIL] No whale detected in 10s")
    return False


# Orchestration
if __name__ == "__main__":
    # Start Hub
    p = multiprocessing.Process(target=run_hub)
    p.start()

    try:
        # Give Hub time to start
        time.sleep(2)

        # Run Listener
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(listen_for_whale())

        if not success:
            exit(1)

    finally:
        p.terminate()
        p.join()
