import structlog
from .l2_aggregator import L2Aggregator

logger = structlog.get_logger(__name__)

class ZmqReceiver:
    def __init__(self):
        self.l2_aggregator = L2Aggregator(max_depth_pct=5.0, wall_multiplier=10.0)
        logger.info("ZmqReceiver initialized")

    async def start(self):
        logger.info("ZmqReceiver started")

    async def stop(self):
        logger.info("ZmqReceiver stopped")

    def handle_message(self, topic: str, payload: dict):
        if topic.endswith("depth_l2"):
            walls = self.l2_aggregator.analyze_orderbook(payload)
            # Передати walls у State або напряму в DCA Engine
            if walls.get("bid_walls") or walls.get("ask_walls"):
                logger.debug("Processed depth_l2 with walls", walls=walls)
