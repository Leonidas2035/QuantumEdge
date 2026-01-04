"""Configuration helpers for the MarketDataHub service."""

from dataclasses import dataclass, field
import os
from typing import List


@dataclass
class ZmqConfig:
    endpoint: str = os.getenv("MARKET_DATA_ZMQ_ENDPOINT", "ipc:///tmp/quantum_market_data.ipc")
    snd_hwm: int = int(os.getenv("MARKET_DATA_ZMQ_SNDHWM", "1000"))
    rcv_hwm: int = int(os.getenv("MARKET_DATA_ZMQ_RCVHWM", "1000"))
    conflate_l1: bool = os.getenv("MARKET_DATA_ZMQ_CONFLATE", "0") in {"1", "true", "True"}


@dataclass
class QuestConfig:
    host: str = os.getenv("MARKET_DATA_QUEST_HOST", "127.0.0.1")
    port: int = int(os.getenv("MARKET_DATA_QUEST_PORT", "9009"))
    batch_rows: int = int(os.getenv("MARKET_DATA_QUEST_BATCH_ROWS", "256"))
    flush_interval_ms: int = int(os.getenv("MARKET_DATA_QUEST_FLUSH_MS", "500"))


@dataclass
class HubConfig:
    symbols: List[str] = field(default_factory=lambda: os.getenv("MARKET_DATA_SYMBOLS", "BTCUSDT").split(","))
    zmq: ZmqConfig = field(default_factory=ZmqConfig)
    quest: QuestConfig = field(default_factory=QuestConfig)
    l0_hwm: int = int(os.getenv("MARKET_DATA_L0_HWM", "2000"))
    l1_hwm: int = int(os.getenv("MARKET_DATA_L1_HWM", "5000"))
    log_level: str = os.getenv("MARKET_DATA_LOG_LEVEL", "INFO")

    @staticmethod
    def load() -> "HubConfig":
        return HubConfig()
