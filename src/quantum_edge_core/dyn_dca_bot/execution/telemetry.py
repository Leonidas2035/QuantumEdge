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
            "source": "dyndca_v1",
            "timestamp": time.time(),
            "status": "RUNNING",
            "pnl_session": float(current_pnl),
            "metrics": {
                "active_positions_count": 1 if abs(position_size) > 1e-8 else 0,
                "position_size": float(position_size),
                "average_entry_price": float(avg_entry),
                "unrealized_pnl": float(current_pnl)
            },
            "errors": []
        }
        self.socket.send_multipart([b"telemetry", json.dumps(payload).encode("utf-8")])
        logger.debug("Published telemetry status", payload=payload)
        
        # Write to QuestDB via ILP writer
        try:
            from quantum_edge_core.market_data.tsdb.ilp_writer import get_ilp_writer
            writer = get_ilp_writer()
            writer.write_row(
                "bot_telemetry",
                symbols={"bot_id": "dyndca_v1", "status": "RUNNING"},
                columns={
                    "pnl_session": float(current_pnl),
                    "active_margin": float(abs(position_size) * avg_entry),
                    "drawdown_pct": 0.0,
                    "latency_ms": 0
                },
                ts=payload["timestamp"]
            )
        except Exception as e:
            logger.warning("Failed to write DynDCA telemetry to QuestDB", error=str(e))
        
    def close(self):
        self.socket.close()
        self.context.term()
