import asyncio
import signal
import sys
import structlog

from quantum_edge_core.dyn_dca_bot.core.config import DynDCAConfig

logger = structlog.get_logger(__name__)

class DynDCAService:
    def __init__(self):
        self.config = DynDCAConfig.load()
        self.running = False

    async def start(self):
        self.running = True
        logger.info("Starting DynDCA Microservice", bot_id=self.config.bot_id)
        
        # TODO: Ініціалізація ZMQ Receiver, L2 Aggregator, DCA Engine
        
        try:
            while self.running:
                # Головний цикл бота (очікування тіків)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.warning("Main loop cancelled")
        finally:
            await self.shutdown()

    async def shutdown(self):
        logger.info("Initiating graceful shutdown for DynDCA...")
        self.running = False
        # TODO: Закриття ZMQ сокетів, скасування активних ордерів
        logger.info("Shutdown complete.")

def handle_exception(loop, context):
    msg = context.get("exception", context["message"])
    logger.error("Caught exception", error=str(msg))

if __name__ == "__main__":
    service = DynDCAService()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Налаштування обробки сигналів для POSIX
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(service.shutdown()))
        except NotImplementedError:
            pass  # Windows fallback

    loop.set_exception_handler(handle_exception)
    
    try:
        loop.run_until_complete(service.start())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
        loop.run_until_complete(service.shutdown())
