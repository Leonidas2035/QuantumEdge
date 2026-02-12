"""
src/quantum_edge_core/core/service.py

Base service class handling lifecycle and signal interruption.
"""

import asyncio
import signal
import structlog
from abc import ABC, abstractmethod

logger = structlog.get_logger()

class BaseService(ABC):
    """
    Abstract Base Class for all long-running services.
    Handles SIGINT/SIGTERM for graceful shutdown.
    """

    def __init__(self, name: str):
        self.name = name
        self.logger = logger.bind(service=name)
        self._shutdown_event = asyncio.Event()

    @abstractmethod
    async def run(self):
        """
        Main loop of the service.
        Must check self._shutdown_event.is_set() or catch CancelledError.
        """
        pass

    async def cleanup(self):
        """
        Override this to close resources (DB connections, sockets) on shutdown.
        """
        pass

    def _handle_signal(self, sig):
        self.logger.info("Signal received, initiating shutdown", signal=sig.name)
        self._shutdown_event.set()

    async def _runner_wrapper(self):
        """
        Wraps the main run loop and ensures cleanup happens.
        """
        loop = asyncio.get_running_loop()
        
        # Register signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: self._handle_signal(s))
            except NotImplementedError:
                # Windows does not support add_signal_handler in some loops, or if not main thread
                self.logger.warning("Signal handlers not supported in this environment")

        self.logger.info("Service starting")
        try:
            # Run the implementation's main loop
            # We wrap it in a task to allow cancellation if needed, 
            # though usually run() should monitor _shutdown_event.
            task = asyncio.create_task(self.run())
            
            # Wait for shutdown signal OR task completion (if it finishes early)
            shutdown_wait_task = asyncio.create_task(self._shutdown_event.wait())
            await asyncio.wait([task, shutdown_wait_task], return_when=asyncio.FIRST_COMPLETED)

            if not task.done():
                # If shutdown event triggered but task running, cancel it
                self.logger.info("Service stopping...")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            else:
                # Task finished locally (maybe error or clean exit)
                try:
                    await task
                except Exception:
                    self.logger.exception("Service crashed")
                    raise

        except Exception as e:
            self.logger.error("Service runtime error", error=str(e))
            raise
        finally:
            self.logger.info("Service cleaning up")
            await self.cleanup()
            self.logger.info("Service stopped")

    @classmethod
    def start(cls, *args, **kwargs):
        """
        Entry point for running the service.
        Note: The actual execution should happen inside an `asyncio.run(service._runner_wrapper())` block,
        but usually services are started via `asyncio.run(main())`.
        """
        raise NotImplementedError("Use instance methods. This is just a placeholder.")
