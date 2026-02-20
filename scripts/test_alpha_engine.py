import asyncio
import multiprocessing
import os
import time
from unittest.mock import MagicMock, patch

import structlog

from quantum_edge_core.bot.service import TradingBot
from quantum_edge_core.market_data.hub import MarketDataHubService
from quantum_edge_core.utils.async_runner import run_service


# Mock Logger capture
class LogCapture:
    def __init__(self):
        self.logs = []

    def __call__(self, logger, method_name, event_dict):
        self.logs.append(event_dict)
        return event_dict


# 1. Hub Process
def run_hub():
    # Run Hub in a separate process
    # Disable QuestDB for this test to avoid conflicting with other running instances if any
    os.environ["QUESTDB_HOST"] = ""
    hub = MarketDataHubService()
    run_service(hub._runner_wrapper())


# 2. Bot Process with Mock Gemini
def run_bot(queue):
    # Mock Gemini Client
    with patch("quantum_edge_core.bot.service.GeminiClient") as MockGemini:
        mock_ai = MockGemini.return_value
        mock_ai.safe_analyze_risk = MagicMock(return_value="safe")

        # Configure structlog to capture logs to check criteria
        structlog.configure(processors=[structlog.processors.JSONRenderer()])

        # We can't easily capture logs from subprocess without complex setup or analyzing stdout.
        # So we will rely on stdout analysis in the main process if we capture it,
        # OR we just let it run and simple-check connection.
        # Actually, let's just run the bot and let it print. The main process will inspect success
        # criteria via ZMQ Snoop or just trust the components if they don't crash.
        # A better way: The Bot logs "Market Metrics Received". We can grep that.

        bot = TradingBot()
        run_service(bot.run())


# 3. Snoop Process (Main)
async def snoop_metrics():
    import zmq.asyncio

    from quantum_edge_core.events import EventCodec, MarketMetrics

    ctx = zmq.asyncio.Context()
    sock = ctx.socket(zmq.SUB)
    sock.connect("tcp://127.0.0.1:5555")
    sock.subscribe("market.metrics")

    print("Snooping for Market Metrics...")

    start = time.time()
    count = 0
    regimes_seen = set()

    while time.time() - start < 15:
        try:
            msg = await asyncio.wait_for(sock.recv_multipart(), timeout=1.0)
            payload = msg[1]
            event = EventCodec.decode(payload)

            if isinstance(event, MarketMetrics):
                count += 1
                regimes_seen.add(event.regime)
                if count % 10 == 0:
                    print(
                        f"Received Metric: Regime={event.regime} VWAP={event.vwap:.2f}"
                    )

                if count >= 20:  # Received enough metrics
                    print(f"[PASS] Successfully streamed {count} metrics.")
                    print(f"Regimes seen: {regimes_seen}")
                    return True

        except asyncio.TimeoutError:
            continue
        except Exception as e:
            print(f"Error: {e}")

    print("[FAIL] Did not receive enough metrics.")
    return False


if __name__ == "__main__":
    # Start Hub
    p_hub = multiprocessing.Process(target=run_hub)
    p_hub.start()

    # Start Bot
    p_bot = multiprocessing.Process(target=run_bot, args=(None,))
    p_bot.start()

    try:
        time.sleep(2)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(snoop_metrics())

        if not success:
            exit(1)

    finally:
        p_hub.terminate()
        p_bot.terminate()
        p_hub.join()
        p_bot.join()
