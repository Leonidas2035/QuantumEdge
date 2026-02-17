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

    async def send_heartbeat(self, state: BotState, pnl: float, open_positions_qty: float, drawdown_pct: float = 0.0):
        """
        Sends a JSON heartbeat packet.
        Schema:
        {
          "service_id": "ai_scalper_bot",
          "timestamp": <unix_epoch_float>,
          "state": "RUNNING",
          "metrics": {
              "pnl_session": <float>,
              "active_positions_count": <int>,
              "current_drawdown_pct": <float>,
              "cpu_usage": <float>
          },
          "errors": []
        }
        """
        msg = {
            "service_id": "ai_scalper_bot",
            "timestamp": time.time(),
            "state": state.name,  # Using BotState name (e.g., RUNNING, IDLE, ERROR)
            "metrics": {
                "pnl_session": float(pnl),
                "active_positions_count": int(open_positions_qty),
                "current_drawdown_pct": float(drawdown_pct),
                "cpu_usage": 0.0
            },
            "errors": []
        }
        
        try:
            # Create JSON string
            payload = ujson.dumps(msg)
            # Send Multipart [topic, payload]
            await self.socket.send_multipart([b"heartbeat", payload.encode("utf-8")])
        except Exception as e:
            logger.warning(f"Failed to send heartbeat: {e}")

    def close(self):
        self.socket.close()
        self.context.term()
