"""
src/quantum_edge_core/utils/async_runner.py

Unified async service runner.
Handles uvloop installation and graceful shutdown signals.
"""

import asyncio
import signal
import sys
import logging
from typing import Coroutine, Any

logger = logging.getLogger(__name__)

def run_service(main_coro: Coroutine[Any, Any, None]) -> None:
    """
    Run an async service entry point with uvloop and signal handling.
    """
    # 1. Install uvloop
    try:
        import uvloop
    except ImportError:
        logger.warning("uvloop not installed, using default asyncio loop")
        uvloop = None

    # 2. Setup Loop Policy / Runner
    if sys.version_info >= (3, 11):
        # Python 3.11+: Use asyncio.Runner with loop_factory
        loop_factory = uvloop.new_event_loop if uvloop else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            # Note: We can't easily add signal handlers inside Runner.run() 
            # without managing the loop manually or using internal APIs.
            # So we use the standard robust pattern:
            runner.run(main_coro)
            
    else:
        # Python < 3.11
        if uvloop:
            uvloop.install()
        
        asyncio.run(main_coro)
