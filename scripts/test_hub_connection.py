
import asyncio
import signal
import uvloop
from quantum_edge_core.core.service import BaseService
from quantum_edge_core.market_data.feeds.binance_feed import BinanceFeed
from quantum_edge_core.logging_setup import setup_logging

class MockHub(BaseService):
    async def run(self):
        self.logger.info("MockHub running")
        while not self._shutdown_event.is_set():
            await asyncio.sleep(0.1)

async def test_base_service_signals():
    print("\n[TEST] BaseService Signal Handling")
    service = MockHub("TestHub")
    
    # Run service in background task
    task = asyncio.create_task(service._runner_wrapper())
    await asyncio.sleep(0.5)
    
    # Simulate SIGTERM (we can't easily send actual signal to self in python script 
    # without killing the test runner, so we simulate the handler call)
    print("Simulating SIGTERM...")
    service._handle_signal(signal.SIGTERM)
    
    try:
        await asyncio.wait_for(task, timeout=2.0)
        print("[PASS] Service shut down gracefully via signal handler simulation")
    except asyncio.TimeoutError:
        print("[FAIL] Service did not shut down in time")

async def test_binance_feed_connect():
    print("\n[TEST] BinanceFeed Connection (Short)")
    feed = BinanceFeed(["btcusdt"])
    
    task = asyncio.create_task(feed._runner_wrapper())
    
    # Let it run for 5 seconds to try and connect
    print("Waiting for connection (5s)...")
    await asyncio.sleep(5)
    
    # Stop it
    print("Stopping feed...")
    feed._handle_signal(signal.SIGINT)
    
    try:
        await asyncio.wait_for(task, timeout=2.0)
        print("[PASS] Feed stopped")
    except asyncio.TimeoutError:
        print("[FAIL] Feed stop timed out")

def main():
    setup_logging()
    uvloop.install()
    
    async def _main_async():
        await test_base_service_signals()
        await test_binance_feed_connect()

    asyncio.run(_main_async())

if __name__ == "__main__":
    main()
