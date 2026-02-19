from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

from quantum_edge_core.strategies.scalper_v1.bot.core.config_loader import \
    config


@dataclass
class QuestDbSettings:
    host: str
    ilp_port: int
    http_port: int
    pg_port: int
    ilp_http_url: str
    query_url: str
    health_url: str


@dataclass
class WriterSettings:
    batch_rows: int
    flush_interval_ms: int


@dataclass
class QueueSettings:
    max_events: int
    max_bytes: int
    drop_policy: str


@dataclass
class SpoolSettings:
    enabled: bool
    path: Path
    max_bytes: int
    retention_days: int
    max_file_bytes: int
    rotation_minutes: int


@dataclass
class RetrySettings:
    max_retries: int
    base_backoff_ms: int
    max_backoff_ms: int


@dataclass
class EventsSettings:
    raw_trades: bool
    market_l1: bool
    bars_1s: bool


@dataclass
class TsdbConfig:
    enabled: bool
    backend: str
    questdb: QuestDbSettings
    writer: WriterSettings
    queue: QueueSettings
    spool: SpoolSettings
    retry: RetrySettings
    events: EventsSettings
    retention_days: Dict[str, int]


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def load_tsdb_config() -> TsdbConfig:
    path = Path(config.qe_root) / "config" / "tsdb.yaml"
    raw = _load_yaml(path)

    backend_raw = str(raw.get("backend", "none")).lower()
    backend = "questdb" if backend_raw in {"questdb_ilp", "questdb"} else backend_raw

    questdb_raw = raw.get("questdb", {}) or {}
    questdb = QuestDbSettings(
        host=str(questdb_raw.get("host", "127.0.0.1")),
        ilp_port=_int(questdb_raw.get("ilp_port", 9009), 9009),
        http_port=_int(questdb_raw.get("http_port", 9000), 9000),
        pg_port=_int(questdb_raw.get("pg_port", 8812), 8812),
        ilp_http_url=str(questdb_raw.get("ilp_http_url", "http://127.0.0.1:9000/imp")),
        query_url=str(questdb_raw.get("query_url", "http://127.0.0.1:9000/exec")),
        health_url=str(questdb_raw.get("health_url", "http://127.0.0.1:9003/health")),
    )

    batch_rows = _int(raw.get("write_batch_rows", raw.get("batch_size", 500)), 500)
    flush_interval_ms = _int(
        raw.get(
            "write_flush_interval_ms",
            _int(raw.get("flush_interval_seconds", 2), 2) * 1000,
        ),
        2000,
    )

    queue_raw = raw.get("queue", {}) or {}
    memory_raw = raw.get("memory_budgets", {}) or {}
    drop_policy = str(queue_raw.get("drop_policy", "drop_lowest")).lower()
    if drop_policy not in {"drop_lowest", "drop_newest"}:
        drop_policy = "drop_lowest"

    queue = QueueSettings(
        max_events=_int(queue_raw.get("max_events", 10000), 10000),
        max_bytes=_int(
            queue_raw.get(
                "max_bytes", memory_raw.get("ingest_queue_bytes", 256 * 1024 * 1024)
            ),
            256 * 1024 * 1024,
        ),
        drop_policy=drop_policy,
    )

    spool_raw = raw.get("spool", {}) or {}
    spool_base = spool_raw.get("path", "runtime/spool")
    spool_path = (
        Path(config.qe_root) / spool_base
        if not Path(spool_base).is_absolute()
        else Path(spool_base)
    )
    spool = SpoolSettings(
        enabled=_bool(spool_raw.get("enabled", True), True),
        path=spool_path,
        max_bytes=_int(
            spool_raw.get(
                "max_bytes", memory_raw.get("spool_max_bytes", 1024 * 1024 * 1024)
            ),
            1024 * 1024 * 1024,
        ),
        retention_days=_int(spool_raw.get("retention_days", 3), 3),
        max_file_bytes=_int(
            spool_raw.get("max_file_bytes", 10 * 1024 * 1024), 10 * 1024 * 1024
        ),
        rotation_minutes=_int(spool_raw.get("rotation_minutes", 5), 5),
    )

    events_raw = raw.get("events", {}) or {}
    events = EventsSettings(
        raw_trades=_bool(events_raw.get("raw_trades", False), False),
        market_l1=_bool(events_raw.get("market_l1", True), True),
        bars_1s=_bool(events_raw.get("bars_1s", True), True),
    )

    retry_raw = raw.get("retry", {}) or {}
    retry = RetrySettings(
        max_retries=_int(retry_raw.get("max_retries", 5), 5),
        base_backoff_ms=_int(retry_raw.get("base_backoff_ms", 200), 200),
        max_backoff_ms=_int(retry_raw.get("max_backoff_ms", 5000), 5000),
    )

    retention_raw = raw.get("retention_days", {}) or {}
    retention_days = {
        "l0_raw": _int(retention_raw.get("l0_raw", 14), 14),
        "l1_bars": _int(retention_raw.get("l1_bars", 180), 180),
        "l2_telemetry": _int(retention_raw.get("l2_telemetry", 180), 180),
    }

    return TsdbConfig(
        enabled=_bool(raw.get("enabled", False), False),
        backend=backend,
        questdb=questdb,
        writer=WriterSettings(
            batch_rows=batch_rows, flush_interval_ms=flush_interval_ms
        ),
        queue=queue,
        spool=spool,
        retry=retry,
        events=events,
        retention_days=retention_days,
    )
