import structlog

logger = structlog.get_logger(__name__)

class GridState:
    def __init__(self):
        self.orders = []
        logger.info("GridState initialized")
