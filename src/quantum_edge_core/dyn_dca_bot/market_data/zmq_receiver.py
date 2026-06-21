import structlog

logger = structlog.get_logger(__name__)

class ZmqReceiver:
    def __init__(self):
        logger.info("ZmqReceiver initialized")

    async def start(self):
        logger.info("ZmqReceiver started")

    async def stop(self):
        logger.info("ZmqReceiver stopped")
