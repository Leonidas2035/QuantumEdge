"""Verification script for Async Supervisor Isolation."""

import asyncio
import logging
import time
from unittest.mock import AsyncMock

# Add src to path
import sys
import os

sys.path.append(os.path.abspath("src"))

from quantum_edge_core.supervisor.service import AsyncSupervisor
from quantum_edge_core.supervisor.supervisor.gemini_client import GeminiClient


async def run_test():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] Test: %(message)s")
    logger = logging.getLogger("TestAsyncIsolation")

    logger.info("Initializing AsyncSupervisor...")
    supervisor = AsyncSupervisor()

    # Mock GeminiClient to be SLOW
    mock_client = AsyncMock(spec=GeminiClient)

    async def slow_mock_analyze(*args, **kwargs):
        logger.info("Mock Gemini: Starting slow request (sleeping 3s)...")
        await asyncio.sleep(3.0)
        logger.info("Mock Gemini: Finished slow request.")
        return {"action": "OK", "comment": "Slow response"}

    mock_client.safe_analyze_risk.side_effect = slow_mock_analyze
    supervisor.gemini_client = mock_client

    # Instrument monitor_loop to count iterations
    original_monitor = supervisor.monitor_loop
    iteration_count = 0

    async def instrumented_monitor():
        nonlocal iteration_count
        supervisor.logger.info("Starting Monitor Loop (Fast)")
        while supervisor.running:
            start_time = time.time()
            # Logic from original
            # Just count tick and check heartbeat validity for simulation
            supervisor.last_heartbeat_time = time.time()  # Keep it alive

            # Count
            iteration_count += 1
            if iteration_count % 10 == 0:
                logger.info(f"Monitor Loop Tick: {iteration_count}")

            elapsed = time.time() - start_time
            sleep_time = max(0.0, 0.1 - elapsed)
            await asyncio.sleep(sleep_time)

    supervisor.monitor_loop = instrumented_monitor

    # Set strategy loop duration to match test duration/frequency for triggering
    # We want strategy loop to trigger at least once.
    # Original strategy loop waits 5s. We might need to shorten it for this test
    # OR run the test for > 5s.
    # Let's override strategy_loop sleep to 0.5s so it triggers quickly and blocks.

    async def test_strategy_loop():
        supervisor.logger.info("Starting Strategy Loop (Modified for Test)")
        while supervisor.running:
            try:
                # Call slow mock
                await supervisor.gemini_client.safe_analyze_risk({})
            except Exception:
                pass
            await asyncio.sleep(0.5)  # Fast retry to keep pressure

    supervisor.strategy_loop = test_strategy_loop

    # Run for 5 seconds
    logger.info("Starting Supervisor for 5 seconds...")
    task = asyncio.create_task(supervisor.run())

    await asyncio.sleep(5.0)
    supervisor.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass

    logger.info(f"Test Finished. Total Monitor Iterations: {iteration_count}")

    # Validation
    # In 5 seconds, at 100ms (10Hz), we expect ~50 ticks.
    # If blocked by 3s sleep, we would see only ~20 ticks (if purely sequential).
    # Since we sleep 3s in strategy, monitor should keep running.
    # We expect close to 50. Let's set a pass threshold of 40 (allow some overhead).

    if iteration_count >= 40:
        logger.info("SUCCESS: Monitor loop ran independently of slow strategy loop.")
        sys.exit(0)
    else:
        logger.error(f"FAILURE: Monitor loop blocked? Iterations: {iteration_count} < 40")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(run_test())
    except KeyboardInterrupt:
        pass
