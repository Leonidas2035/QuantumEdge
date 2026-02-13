"""
Supervisor Reporter.
Publishes bot health and status metrics to the Supervisor system via ZMQ.
"""

import zmq
import ujson
import time
import logging
from zmq.asyncio import Context as AsyncContext

from quantum_edge_core.ai_scalper_bot.bot.execution.strategy_core import BotState

logger = logging.getLogger(__name__)


class SupervisorReporter:
    """
    Publishes heartbeat and metrics to a ZMQ PUB socket.
    """

    def __init__(self, pub_endpoint: str = "tcp://*:5557"):
        self.context = AsyncContext()
        self.socket = self.context.socket(zmq.PUB)
        try:
            self.socket.bind(pub_endpoint)
            logger.info(f"SupervisorReporter bound to {pub_endpoint}")
        except zmq.ZMQError as e:
            logger.error(f"Failed to bind SupervisorReporter: {e}")
            raise

    async def send_heartbeat(self, state: BotState, pnl: float, open_positions_qty: float):
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
            # Create JSON string
            payload = ujson.dumps(msg)
            # Send
            await self.socket.send_string(payload)
        except Exception as e:
            logger.warning(f"Failed to send heartbeat: {e}")

    def close(self):
        self.socket.close()
        self.context.term()
