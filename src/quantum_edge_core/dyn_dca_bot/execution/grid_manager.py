import structlog

logger = structlog.get_logger(__name__)

class GridManager:
    def __init__(self):
        logger.info("GridManager initialized")

    async def start(self):
        logger.info("GridManager started")

    async def stop(self):
        logger.info("GridManager stopped")
