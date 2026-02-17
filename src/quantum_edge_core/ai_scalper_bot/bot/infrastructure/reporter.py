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
    def __init__(self, pub_endpoint: str = "tcp://*:5557", service_id: str = "ai_scalper_bot"):
        self.service_id = service_id
        self.context = AsyncContext()
        self.socket = self.context.socket(zmq.PUB)
        try:
            self.socket.bind(pub_endpoint)
            logger.info(f"SupervisorReporter bound to {pub_endpoint} with ID {service_id}")
        except zmq.ZMQError as e:
            logger.error(f"Failed to bind SupervisorReporter: {e}")
            raise

    async def send_heartbeat(self, state: BotState, pnl: float, open_positions_qty: float, drawdown_pct: float = 0.0):
        """
        Sends a JSON heartbeat packet.
        Schema:
        {
          "source": "ai_scalper_bot",
          "timestamp": <unix_epoch_float>,
          "status": "RUNNING",
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
            "source": self.service_id,
            "timestamp": time.time(),
            "status": state.name,  # Using BotState name (e.g., RUNNING, IDLE, ERROR)
            "pnl_session": float(pnl),
            "drawdown_pct": float(drawdown_pct),
            "metrics": {
                "active_positions_count": int(open_positions_qty),
                "cpu_usage": 0.0
            },
            "errors": []
        }
        
        try:
            # Create JSON string
            payload = ujson.dumps(msg)
            # Send Multipart [topic, payload]
            await self.socket.send_multipart([b"telemetry", payload.encode("utf-8")])
        except Exception as e:
            logger.warning(f"Failed to send heartbeat: {e}")

    def close(self):
        self.socket.close()
        self.context.term()
