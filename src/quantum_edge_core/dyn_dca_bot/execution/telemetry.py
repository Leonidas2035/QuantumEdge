import zmq
import json
import structlog
import time

logger = structlog.get_logger(__name__)

class BotPublisher:
    def __init__(self, port: int = 5567):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind(f"tcp://127.0.0.1:{port}")
        logger.info("Telemetry publisher initialized", port=port)

    def publish_status(self, position_size: float, avg_entry: float, current_pnl: float):
        """
        Відправляє StatusEnvelope для Supervisor Agent.
        """
        payload = {
            "bot_id": "dyndca_v1",
            "timestamp": time.time(),
            "position_size": position_size,
            "average_entry_price": avg_entry,
            "unrealized_pnl": current_pnl,
            "state": "RUNNING"
        }
        self.socket.send_string(f"status.dyndca {json.dumps(payload)}")
        logger.debug("Published telemetry status", payload=payload)
        
    def close(self):
        self.socket.close()
        self.context.term()
