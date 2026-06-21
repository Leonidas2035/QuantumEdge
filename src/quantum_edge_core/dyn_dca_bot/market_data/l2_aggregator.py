import structlog

logger = structlog.get_logger(__name__)

class L2Aggregator:
    def __init__(self):
        logger.info("L2Aggregator initialized")

    async def start(self):
        logger.info("L2Aggregator started")

    async def stop(self):
        logger.info("L2Aggregator stopped")
