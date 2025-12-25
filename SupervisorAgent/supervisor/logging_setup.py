"""Logging configuration for SupervisorAgent."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path


def setup_logging(logs_dir: Path) -> logging.Logger:
    """Configure root logging with rotating file and console handlers."""

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "supervisor.log"

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if not any(isinstance(h, logging.StreamHandler) and h.stream is sys.stdout for h in logger.handlers):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger

