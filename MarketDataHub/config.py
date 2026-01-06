"""Configuration helpers for the MarketDataHub service."""

from dataclasses import dataclass, field
import os
from typing import Dict, List


@dataclass
class ZmqConfig:
    endpoint: str = os.getenv("MARKET_DATA_ZMQ_ENDPOINT", "ipc:///tmp/quantum_market_data.ipc")
    snd_hwm: int = int(os.getenv("MARKET_DATA_ZMQ_SNDHWM", "1000"))
    rcv_hwm: int = int(os.getenv("MARKET_DATA_ZMQ_RCVHWM", "1000"))
    conflate_l1: bool = os.getenv("MARKET_DATA_ZMQ_CONFLATE", "0") in {"1", "true", "True"}


@dataclass
class SnapshotConfig:
    endpoint: str = os.getenv("MARKET_DATA_SNAPSHOT_ENDPOINT", "ipc:///tmp/quantum_market_snapshot.ipc")
    trade_tail: int = int(os.getenv("MARKET_DATA_SNAPSHOT_TRADE_TAIL", "0"))
    timeout_ms: int = int(os.getenv("MARKET_DATA_SNAPSHOT_TIMEOUT_MS", "500"))


@dataclass
class QuestConfig:
    host: str = os.getenv("MARKET_DATA_QUEST_HOST", "127.0.0.1")
    port: int = int(os.getenv("MARKET_DATA_QUEST_PORT", "9009"))
    batch_rows: int = int(os.getenv("MARKET_DATA_QUEST_BATCH_ROWS", "256"))
    flush_interval_ms: int = int(os.getenv("MARKET_DATA_QUEST_FLUSH_MS", "500"))


@dataclass
class TsdbConfig:
    enabled: bool = os.getenv("MARKET_DATA_TSDB_ENABLED", "1") in {"1", "true", "True"}
    host: str = os.getenv("MARKET_DATA_TSDB_HOST", "127.0.0.1")
    ilp_port: int = int(os.getenv("MARKET_DATA_TSDB_ILP_PORT", "9009"))
    batch_rows: int = int(os.getenv("MARKET_DATA_TSDB_BATCH_ROWS", "1024"))
    flush_interval_ms: int = int(os.getenv("MARKET_DATA_TSDB_FLUSH_MS", "200"))
    l1_conflate: bool = os.getenv("MARKET_DATA_TSDB_L1_CONFLATE", "1") in {"1", "true", "True"}
    bars_queue_max: int = int(os.getenv("MARKET_DATA_TSDB_BARS_QUEUE_MAX", "5000"))
    store_trades_raw: bool = os.getenv("MARKET_DATA_TSDB_STORE_TRADES_RAW", "0") in {"1", "true", "True"}


@dataclass
class L2Config:
    enabled: bool = os.getenv("MARKET_DATA_L2_ENABLED", "1") in {"1", "true", "True"}
    spool_dir: str = os.getenv("MARKET_DATA_L2_SPOOL_DIR", "spool/l2")
    rotate_mb: int = int(os.getenv("MARKET_DATA_L2_ROTATE_MB", "64"))
    flush_interval_ms: int = int(os.getenv("MARKET_DATA_L2_FLUSH_MS", "200"))
    fsync_on_rotate: bool = os.getenv("MARKET_DATA_L2_FSYNC_ON_ROTATE", "0") in {"1", "true", "True"}
    buffer_max: int = int(os.getenv("MARKET_DATA_L2_BUFFER_MAX", "1024"))
    max_spool_gb: int = int(os.getenv("MARKET_DATA_L2_MAX_SPOOL_GB", "50"))
    on_budget_exceeded: str = os.getenv("MARKET_DATA_L2_BUDGET_MODE", "block").lower()

    @property
    def max_spool_bytes(self) -> int:
        return int(self.max_spool_gb * 1024 * 1024 * 1024)


@dataclass
class WallsConfig:
    enabled: bool = os.getenv("MARKET_DATA_WALLS_ENABLED", "1") in {"1", "true", "True"}
    per_symbol_threshold_qty: Dict[str, float] = field(
        default_factory=lambda: {"BTCUSDT": float(os.getenv("MARKET_DATA_WALLS_BTC_THRESHOLD", "50.0"))}
    )
    default_threshold_notional_usd: float = float(os.getenv("MARKET_DATA_WALLS_THRESHOLD_USD", "2000000"))
    top_k: int = int(os.getenv("MARKET_DATA_WALLS_TOP_K", "20"))
    max_distance_bps: int = int(os.getenv("MARKET_DATA_WALLS_MAX_DISTANCE_BPS", "500"))


@dataclass
class OrderbookSnapshotConfig:
    include_depth: bool = os.getenv("MARKET_DATA_ORDERBOOK_SNAPSHOT_DEPTH", "1") in {"1", "true", "True"}
    include_walls: bool = os.getenv("MARKET_DATA_ORDERBOOK_SNAPSHOT_WALLS", "1") in {"1", "true", "True"}


@dataclass
class OrderbookConfig:
    enabled: bool = os.getenv("MARKET_DATA_ORDERBOOK_ENABLED", "1") in {"1", "true", "True"}
    symbols: List[str] = field(default_factory=lambda: os.getenv("MARKET_DATA_ORDERBOOK_SYMBOLS", "BTCUSDT,ETHUSDT").split(","))
    top_n_levels: int = int(os.getenv("MARKET_DATA_ORDERBOOK_TOP_N", "50"))
    publish_interval_ms: int = int(os.getenv("MARKET_DATA_ORDERBOOK_PUBLISH_MS", "100"))
    walls: WallsConfig = field(default_factory=WallsConfig)
    snapshot: OrderbookSnapshotConfig = field(default_factory=OrderbookSnapshotConfig)


@dataclass
class AccountConfig:
    spot_api_key: str = os.getenv("BINANCE_API_KEY", "")
    spot_api_secret: str = os.getenv("BINANCE_API_SECRET", "")
    usdm_api_key: str = os.getenv("BINANCE_FAPI_KEY", "") or os.getenv("BINANCE_API_KEY", "")
    usdm_api_secret: str = os.getenv("BINANCE_FAPI_SECRET", "") or os.getenv("BINANCE_API_SECRET", "")
    repair_interval_sec: int = int(os.getenv("BINANCE_ACCOUNT_REPAIR_INTERVAL", "1800"))
    base_url: str = "https://api.binance.com"
    fapi_url: str = "https://fapi.binance.com"
    recv_window: int = 5000



@dataclass
class HubConfig:
    symbols: List[str] = field(default_factory=lambda: os.getenv("MARKET_DATA_SYMBOLS", "BTCUSDT").split(","))
    zmq: ZmqConfig = field(default_factory=ZmqConfig)
    snapshot: SnapshotConfig = field(default_factory=SnapshotConfig)
    quest: QuestConfig = field(default_factory=QuestConfig)
    tsdb: TsdbConfig = field(default_factory=TsdbConfig)
    l2: L2Config = field(default_factory=L2Config)
    orderbook: OrderbookConfig = field(default_factory=OrderbookConfig)
    account: "AccountConfig" = field(default_factory=lambda: AccountConfig())
    l0_hwm: int = int(os.getenv("MARKET_DATA_L0_HWM", "2000"))
    l1_hwm: int = int(os.getenv("MARKET_DATA_L1_HWM", "5000"))
    log_level: str = os.getenv("MARKET_DATA_LOG_LEVEL", "INFO")
    status_file: str = os.getenv("MARKET_DATA_STATUS_FILE", "runtime/status/marketdatahub.json")
    status_interval_sec: int = int(os.getenv("MARKET_DATA_STATUS_INTERVAL_SEC", "10"))

    @staticmethod
    def load() -> "HubConfig":
        return HubConfig()
