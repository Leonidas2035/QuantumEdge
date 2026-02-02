"""Lightweight telemetry emitter for bot events."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, Optional
from urllib import request, error


EVENT_VERSION = "telemetry.v1"


@dataclass
class TelemetryConfig:
    enabled: bool = True
    sink: str = "http"  # http | file | both
    http_url: str = "http://127.0.0.1:8765/api/v1/telemetry/ingest"
    file_path: Path = Path("runtime/telemetry.jsonl")
    flush_interval_sec: float = 1.0
    max_queue: int = 1000
    timeout_s: float = 0.3
    max_event_kb: int = 32


class TelemetryEmitter:
    def __init__(self, cfg: TelemetryConfig) -> None:
        self.cfg = cfg
        self.enabled = bool(cfg.enabled)
        self._sink_http = cfg.sink in {"http", "both"}
        self._sink_file = cfg.sink in {"file", "both"}
        self._queue: Deque[Dict[str, Any]] = deque(maxlen=cfg.max_queue)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._dropped = 0
        self._last_flush = 0.0

        if self.enabled and (self._sink_http or self._sink_file):
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def emit_event(self, event_type: str, data: Dict[str, Any], symbol: Optional[str] = None, source: str = "ai_scalper_bot") -> None:
        if not self.enabled:
            return
        payload = {
            "event_version": EVENT_VERSION,
            "ts": int(time.time()),
            "source": source,
            "type": str(event_type),
            "symbol": symbol,
            "data": data or {},
        }
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if len(raw.encode("utf-8")) > self.cfg.max_event_kb * 1024:
            self._dropped += 1
            return
        with self._lock:
            if len(self._queue) >= self.cfg.max_queue:
                self._dropped += 1
                return
            self._queue.append(payload)

    def close(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def dropped(self) -> int:
        return self._dropped

    def _worker(self) -> None:
        while not self._stop.is_set():
            payload = None
            with self._lock:
                if self._queue:
                    payload = self._queue.popleft()
            if payload is None:
                time.sleep(0.05)
                continue
            if self._sink_http:
                self._send_http(payload)
            if self._sink_file:
                self._append_file(payload)

    def _send_http(self, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.cfg.http_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            request.urlopen(req, timeout=self.cfg.timeout_s)  # noqa: S310
        except error.URLError:
            return

    def _append_file(self, payload: Dict[str, Any]) -> None:
        now = time.time()
        if now - self._last_flush < self.cfg.flush_interval_sec:
            return
        self._last_flush = now
        path = Path(self.cfg.file_path)
        if not path.is_absolute():
            qe_root = Path(os.getenv("QE_ROOT") or Path(__file__).resolve().parents[2])
            path = (qe_root / path).resolve()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False))
                handle.write("\n")
        except OSError:
            return
