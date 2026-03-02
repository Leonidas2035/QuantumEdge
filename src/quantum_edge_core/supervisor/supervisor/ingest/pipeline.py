"""TSDB ingestion pipeline from runtime artifacts."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, Optional

from quantum_edge_core.supervisor.supervisor.ingest.parsers import (
    event_hash,
    event_to_point,
    exec_to_point,
    metrics_to_point,
    parse_event_line,
    parse_exec_line,
    parse_metrics_file,
)
from quantum_edge_core.supervisor.supervisor.ingest.tailer import FileTailer
from quantum_edge_core.supervisor.supervisor.tsdb.writer import TsdbWriter


@dataclass
class IngestState:
    events_offset: int = 0
    exec_offset: int = 0
    last_event_ts: Optional[str] = None
    last_exec_ts: Optional[str] = None
    last_metrics_ts: Optional[str] = None
    seen_hashes: Deque[str] = field(default_factory=deque)
    malformed_events: int = 0
    dropped_events: int = 0
    last_updated: Optional[str] = None


class IngestPipeline:
    def __init__(
        self,
        events_path: Path,
        metrics_path: Path,
        exec_path: Optional[Path],
        state_path: Path,
        writer: TsdbWriter,
        max_line_kb: int = 256,
        dedupe_cache_size: int = 5000,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.events_path = Path(events_path)
        self.metrics_path = Path(metrics_path)
        self.exec_path = Path(exec_path) if exec_path else None
        self.state_path = Path(state_path)
        self.writer = writer
        self.max_line_kb = max(int(max_line_kb), 1)
        self.dedupe_cache_size = max(int(dedupe_cache_size), 100)
        self.logger = logger or logging.getLogger(__name__)
        self.state = self._load_state()

    def run_once(self) -> Dict[str, object]:
        ingested = {"events": 0, "metrics": 0, "exec": 0}
        self._ingest_events(ingested)
        self._ingest_metrics(ingested)
        self._ingest_exec(ingested)
        self.state.last_updated = _iso_utc()
        self._save_state()
        return {
            "ingested": ingested,
            "last_event_ts": self.state.last_event_ts,
            "last_metrics_ts": self.state.last_metrics_ts,
            "last_exec_ts": self.state.last_exec_ts,
            "malformed_events": self.state.malformed_events,
            "dropped_events": self.state.dropped_events,
        }

    def run_forever(self, interval_sec: float, stop_path: Path) -> None:
        while True:
            if stop_path.exists():
                self.logger.info("Ingest stop requested: %s", stop_path)
                break
            self.run_once()
            time.sleep(max(interval_sec, 0.5))

    def status(self) -> Dict[str, object]:
        now = time.time()
        event_lag = _lag_seconds(self.state.last_event_ts, now)
        metrics_lag = _lag_seconds(self.state.last_metrics_ts, now)
        exec_lag = _lag_seconds(self.state.last_exec_ts, now)
        return {
            "events_offset": self.state.events_offset,
            "exec_offset": self.state.exec_offset,
            "last_event_ts": self.state.last_event_ts,
            "last_metrics_ts": self.state.last_metrics_ts,
            "last_exec_ts": self.state.last_exec_ts,
            "event_lag_sec": event_lag,
            "metrics_lag_sec": metrics_lag,
            "exec_lag_sec": exec_lag,
            "malformed_events": self.state.malformed_events,
            "dropped_events": self.state.dropped_events,
            "last_updated": self.state.last_updated,
        }

    def _ingest_events(self, counts: Dict[str, int]) -> None:
        tailer = FileTailer(self.events_path, max_line_kb=self.max_line_kb)
        result = tailer.read_new_lines(self.state.events_offset)
        if result.reset:
            self.state.events_offset = 0
        self.state.events_offset = result.offset
        self.state.dropped_events += result.dropped_lines
        for line in result.lines:
            digest = event_hash(line)
            if digest in self.state.seen_hashes:
                continue
            event = parse_event_line(line)
            if not event:
                self.state.malformed_events += 1
                continue
            point = event_to_point(event, digest)
            if point:
                self.writer.enqueue([point])
                counts["events"] += 1
                self.state.last_event_ts = point.ts.isoformat().replace("+00:00", "Z")
            self._remember_hash(digest)

    def _ingest_metrics(self, counts: Dict[str, int]) -> None:
        payload = parse_metrics_file(self.metrics_path)
        if not payload:
            return
        point = metrics_to_point(payload)
        if not point:
            return
        ts = point.ts.isoformat().replace("+00:00", "Z")
        if ts == self.state.last_metrics_ts:
            return
        self.writer.enqueue([point])
        self.state.last_metrics_ts = ts
        counts["metrics"] += 1

    def _ingest_exec(self, counts: Dict[str, int]) -> None:
        if not self.exec_path:
            return
        tailer = FileTailer(self.exec_path, max_line_kb=self.max_line_kb)
        result = tailer.read_new_lines(self.state.exec_offset)
        if result.reset:
            self.state.exec_offset = 0
        self.state.exec_offset = result.offset
        self.state.dropped_events += result.dropped_lines
        for line in result.lines:
            digest = event_hash(line)
            if digest in self.state.seen_hashes:
                continue
            payload = parse_exec_line(line)
            if not payload:
                self.state.malformed_events += 1
                continue
            point = exec_to_point(payload)
            if point:
                self.writer.enqueue([point])
                counts["exec"] += 1
                self.state.last_exec_ts = point.ts.isoformat().replace("+00:00", "Z")
            self._remember_hash(digest)

    def _remember_hash(self, digest: str) -> None:
        self.state.seen_hashes.append(digest)
        while len(self.state.seen_hashes) > self.dedupe_cache_size:
            self.state.seen_hashes.popleft()

    def _load_state(self) -> IngestState:
        if not self.state_path.exists():
            return IngestState(seen_hashes=deque(maxlen=self.dedupe_cache_size))
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return IngestState(seen_hashes=deque(maxlen=self.dedupe_cache_size))
        seen = deque(payload.get("seen_hashes", []), maxlen=self.dedupe_cache_size)
        return IngestState(
            events_offset=int(payload.get("events_offset", 0)),
            exec_offset=int(payload.get("exec_offset", 0)),
            last_event_ts=payload.get("last_event_ts"),
            last_exec_ts=payload.get("last_exec_ts"),
            last_metrics_ts=payload.get("last_metrics_ts"),
            seen_hashes=seen,
            malformed_events=int(payload.get("malformed_events", 0)),
            dropped_events=int(payload.get("dropped_events", 0)),
            last_updated=payload.get("last_updated"),
        )

    def _save_state(self) -> None:
        payload = {
            "events_offset": self.state.events_offset,
            "exec_offset": self.state.exec_offset,
            "last_event_ts": self.state.last_event_ts,
            "last_exec_ts": self.state.last_exec_ts,
            "last_metrics_ts": self.state.last_metrics_ts,
            "seen_hashes": list(self.state.seen_hashes),
            "malformed_events": self.state.malformed_events,
            "dropped_events": self.state.dropped_events,
            "last_updated": self.state.last_updated,
        }
        _atomic_write_json(self.state_path, payload)


def _lag_seconds(value: Optional[str], now_ts: float) -> Optional[int]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    return max(0, int(now_ts - ts))


def _atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        Path(tmp_path).replace(path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
