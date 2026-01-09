"""Config loader for LockBotBTC."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


@dataclass
class LockbotConfig:
    bot_id: str = "LockBotBTC"
    symbol: str = "BTCUSDT"
    hub_sub_endpoint: str = os.getenv("LOCKBOT_HUB_SUB_ENDPOINT", "ipc:///tmp/quantum_market_data.ipc")
    supervisor_cmd_sub_endpoint: str = os.getenv("LOCKBOT_CMD_SUB_ENDPOINT", "ipc:///tmp/lockbot_cmd.ipc")
    bot_pub_endpoint: str = os.getenv("LOCKBOT_PUB_ENDPOINT", "ipc:///tmp/lockbot_status.ipc")
    market_topics: List[str] = field(
        default_factory=lambda: [
            "BTCUSDT:mark_price_1s",
            "BTCUSDT:vwap_d",
            "BTCUSDT:vwap_bands_d",
            "BTCUSDT:avwap",
            "BTCUSDT:liq_heatmap",
        ]
    )
    account_topics: List[str] = field(default_factory=list)
    heartbeat_interval_ms: int = int(os.getenv("LOCKBOT_HEARTBEAT_MS", "1000"))
    cmd_ttl_ms: int = int(os.getenv("LOCKBOT_CMD_TTL_MS", "2000"))
    cmd_cache_size: int = int(os.getenv("LOCKBOT_CMD_CACHE_SIZE", "256"))
    log_level: str = os.getenv("LOCKBOT_LOG_LEVEL", "INFO")
    log_path: str = os.getenv("LOCKBOT_LOG_PATH", "runtime/lockbot_btc.log")

    @staticmethod
    def load(path: Path | None = None) -> "LockbotConfig":
        if path is None or not path.exists():
            return LockbotConfig()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg = LockbotConfig()
        for key, value in (data or {}).items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg

