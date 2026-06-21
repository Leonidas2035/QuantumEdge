import structlog

logger = structlog.get_logger(__name__)

class OrderRouter:
    def __init__(self):
        logger.info("OrderRouter initialized")

    async def start(self):
        logger.info("OrderRouter started")

    async def stop(self):
        logger.info("OrderRouter stopped")
