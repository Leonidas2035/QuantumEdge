"""
src/quantum_edge_core/logging_setup.py

Standardized logging configuration using structlog.
Controls format (JSON/Console) via QE_LOG_FORMAT environment variable.
Adds RotatingFileHandler (50 MB × 5 backups) to prevent disk exhaustion.
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

import structlog

# Disk safety: hard limits for log rotation
_LOG_MAX_BYTES: int = 52_428_800  # 50 MB
_LOG_BACKUP_COUNT: int = 5  # 5 rotated files
_LOG_FILE_PATH: str = os.getenv("QE_LOG_FILE", "logs/quantum_edge.log")


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
    fmt = structlog.stdlib.ProcessorFormatter(processor=formatter_processor)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    # ── Rotating file handler (disk safety) ──────────────────────────
    log_path = Path(_LOG_FILE_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_path),
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    root_logger = logging.getLogger()
    root_logger.handlers = [console_handler, file_handler]

    # Set default level (can be controlled via env var in future)
    root_logger.setLevel(logging.INFO)

    # Redirect standard logging to structlog is implicit via Handler/Formatter above,
    # but we prevent duplicate logs if needed.
