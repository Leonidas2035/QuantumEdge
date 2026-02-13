"""
src/quantum_edge_core/logging_setup.py

Standardized logging configuration using structlog.
Controls format (JSON/Console) via QE_LOG_FORMAT environment variable.
"""

import logging
import os
import sys

import structlog


def setup_logging() -> None:
    """
    Configures structlog and standard library logging.
    """

    # Common processors for both JSON and Console
    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # Determine renderer based on env var
    log_format = os.environ.get("QE_LOG_FORMAT", "console").lower()

    if log_format == "json":
        # JSON Renderer for production/GKE
        processors.append(structlog.processors.format_exc_info)
        processors.append(structlog.processors.JSONRenderer())
        formatter_processor = structlog.processors.JSONRenderer()
    else:
        # Console Renderer for local development
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
        formatter_processor = structlog.dev.ConsoleRenderer(colors=True)

    # Configure structlog
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure Standard Library Logging to use structlog
    # This ensures logs from libraries (like uvicorn, asyncio) are formatted correctly
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=formatter_processor,
        )
    )

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]

    # Set default level (can be controlled via env var in future)
    root_logger.setLevel(logging.INFO)

    # Redirect standard logging to structlog is implicit via Handler/Formatter above,
    # but we prevent duplicate logs if needed.
