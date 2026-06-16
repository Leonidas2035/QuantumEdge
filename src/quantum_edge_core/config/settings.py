"""Unified system configuration via pydantic-settings.

Provides a single source of truth for ZMQ port allocations and execution
parameters.  Reads defaults from this file, overrides from environment
variables prefixed with ``QE_`` (nested: ``QE_PORTS__HUB_PUB_PORT``).

Usage::

    from quantum_edge_core.config.settings import get_settings

    cfg = get_settings()
    hub_endpoint = f"tcp://127.0.0.1:{cfg.ports.hub_pub_port}"
"""

from __future__ import annotations

import logging
from enum import Enum
from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger: logging.Logger = logging.getLogger(__name__)


# ── Execution mode literal ────────────────────────────────────────────
class ExecutionMode(str, Enum):
    """Allowed execution modes for the trading system."""

    LIVE = "live"
    PAPER = "paper"
    DEMO = "demo"
    MOCK = "mock"


# ── ZMQ port registry ────────────────────────────────────────────────
class ZmqRegistry(BaseSettings):
    """Centralised ZMQ port allocation.

    Every port used anywhere in the system **must** be declared here so
    that there is exactly one place to audit for conflicts.
    """

    model_config = SettingsConfigDict(
        env_prefix="QE_PORTS__",
        extra="ignore",
    )

    hub_pub_port: int = Field(
        default=5555,
        ge=1024,
        le=65535,
        description="MarketDataHub PUB socket port",
    )
    telemetry_port: int = Field(
        default=5557,
        ge=1024,
        le=65535,
        description="Bot → Dashboard telemetry PUB port",
    )
    policy_port: int = Field(
        default=5558,
        ge=1024,
        le=65535,
        description="Supervisor → Bot policy/command SUB port",
    )
    heartbeat_port: int = Field(
        default=8765,
        ge=1024,
        le=65535,
        description="Supervisor HTTP heartbeat port",
    )
    questdb_port: int = Field(
        default=9009,
        ge=1024,
        le=65535,
        description="QuestDB ILP ingestion port",
    )


# ── Top-level system settings ────────────────────────────────────────
class SystemSettings(BaseSettings):
    """Root configuration object for the QuantumEdge platform.

    Environment variables are mapped with the ``QE_`` prefix::

        QE_EXECUTION_MODE=paper
        QE_SYMBOLS='["BTCUSDT","ETHUSDT"]'
        QE_PORTS__HUB_PUB_PORT=5555
    """

    model_config = SettingsConfigDict(
        env_prefix="QE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    execution_mode: ExecutionMode = Field(
        default=ExecutionMode.PAPER,
        description="System-wide execution mode",
    )
    symbols: List[str] = Field(
        default=["BTCUSDT"],
        description="List of trading symbols",
    )
    ports: ZmqRegistry = Field(default_factory=ZmqRegistry)

    @field_validator("execution_mode", mode="before")
    @classmethod
    def _normalise_execution_mode(cls, v: object) -> str:
        """Accept upper/mixed-case strings like ``LIVE`` or ``Paper``."""
        if isinstance(v, str):
            return v.strip().lower()
        return v  # type: ignore[return-value]


# ── Singleton accessor ────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_settings() -> SystemSettings:
    """Return the cached, immutable ``SystemSettings`` instance."""
    settings = SystemSettings()
    logger.info(
        "SystemSettings loaded: mode=%s, symbols=%s, ports=%s",
        settings.execution_mode.value,
        settings.symbols,
        settings.ports.model_dump(),
    )
    return settings
