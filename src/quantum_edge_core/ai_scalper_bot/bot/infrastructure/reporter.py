"""
Telemetry Publisher.
Publishes bot health and status metrics to the Supervisor system via ZMQ.
"""

import zmq
import ujson
import time
import asyncio
import logging
from zmq.asyncio import Context as AsyncContext

from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import BotState

logger = logging.getLogger(__name__)


class TelemetryPublisher:
    """
    Publishes heartbeat and metrics to a ZMQ PUB socket.
    """

    def __init__(self, pub_endpoint: str = "tcp://*:5557"):
        self.context = AsyncContext()
        self.socket = self.context.socket(zmq.PUB)
        self._pub_endpoint = pub_endpoint
        self._running = False
        self._task = None
        self._queue = asyncio.Queue(maxsize=100)

    async def start(self):
        """Starts the publisher background task."""
        if self._running:
            return

        try:
            self.socket.bind(self._pub_endpoint)
            logger.info(f"TelemetryPublisher bound to {self._pub_endpoint}")
            self._running = True
            self._task = asyncio.create_task(self._run_loop())
        except zmq.ZMQError as e:
            logger.error(f"Failed to bind TelemetryPublisher: {e}")
            raise

    async def _run_loop(self):
        while self._running:
            try:
                msg = await self._queue.get()
                payload = ujson.dumps(msg)
                await self.socket.send_string(payload)
                self._queue.task_done()
            except Exception as e:
                logger.warning(f"Telemetry loop error: {e}")
                await asyncio.sleep(0.1)

    async def send_heartbeat(
        self, state: BotState, pnl: float, open_positions_qty: float
    ):
        """
        Sends a JSON heartbeat packet.
        Format: {"type": "heartbeat", "ts": ..., "state": "HEDGED", ...}
        """
        msg = {
            "type": "heartbeat",
            "ts": time.time(),
            "service": "ai_scalper_bot",
            "state": state.name,
            "pnl": pnl,
            "position": open_positions_qty,
        }

        try:
            self._queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.socket.close()
        self.context.term()

    def close(self):
        # Synchronous close for legacy support or immediate teardown
        self._running = False
        self.socket.close()
        self.context.term()
