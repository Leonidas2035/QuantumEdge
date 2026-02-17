import logging
from logging.handlers import RotatingFileHandler
import os
from typing import Optional


def _ensure_log_dir(runtime_dir: str) -> str:
    log_dir = os.path.join(runtime_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def configure_logger(
    name: str,
    runtime_dir: str,
    level: str = "INFO",
    run_id: Optional[str] = None,
    log_filename: str = "meta_agent.log",
) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_dir = _ensure_log_dir(runtime_dir)
    log_path = os.path.join(log_dir, log_filename)

    level_value = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(level_value)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s run_id=%(run_id)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(level_value)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    logger.propagate = False

    if run_id is not None:
        logger = logging.LoggerAdapter(logger, extra={"run_id": run_id})
    else:
        logger = logging.LoggerAdapter(logger, extra={"run_id": "-"})
    return logger
