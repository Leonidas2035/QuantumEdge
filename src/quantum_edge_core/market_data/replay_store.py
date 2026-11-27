"""RAM-First MarketSnapshotRecorder and deterministic MarketReplayReader (QE-04).

Uses ``BufferedJsonlArchiver`` from ``storage_policy.py`` so recorded market
bundles stay strictly in RAM until batch-flushed or explicitly closed (zero
per-cycle streaming writes to SSD).
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from quantum_edge_core.execution.storage_policy import (
    BufferedJsonlArchiver,
    get_ram_tmp_dir,
)
from quantum_edge_core.orchestrator.deterministic_orchestrator import PreFetchedBundle


class MarketSnapshotRecorder:
    """RAM-buffered recorder persisting PreFetchedBundles in batches for replay/ablation."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        flush_max_items: int = 32,
        flush_interval_sec: float = 60.0,
    ) -> None:
        target = (
            Path(path)
            if path is not None
            else get_ram_tmp_dir("replay") / f"replay_{int(time.time())}_{uuid.uuid4().hex[:6]}.jsonl"
        )
        self._archiver = BufferedJsonlArchiver(
            target,
            flush_max_items=flush_max_items,
            flush_interval_sec=flush_interval_sec,
        )
        self.records_appended: int = 0

    @property
    def path(self) -> Path:
        return self._archiver.path

    @property
    def pending_count(self) -> int:
        return self._archiver.pending_count

    def record_bundle(
        self,
        bundle: PreFetchedBundle | Any,
        *,
        extra_meta: Mapping[str, Any] | None = None,
    ) -> bool:
        """Append a PreFetchedBundle to the in-RAM buffer; returns True if batch flushed."""
        row: dict[str, Any] = {
            "schema": "QE_REPLAY_BUNDLE_V1",
            "recorded_utc": datetime.now(timezone.utc).isoformat(),
            "snapshot_id": str(getattr(bundle, "snapshot_id", "")),
            "fetch_ms": float(getattr(bundle, "fetch_ms", 0.0) or 0.0),
            "vst_status": dict(getattr(bundle, "vst_status", None) or {}),
            "contexts_by_symbol": dict(getattr(bundle, "contexts_by_symbol", None) or {}),
            "market_leader": getattr(bundle, "market_leader", None),
        }
        if extra_meta:
            row["meta"] = dict(extra_meta)
        self.records_appended += 1
        return self._archiver.append(row)

    def flush(self) -> int:
        return self._archiver.flush()

    def close(self) -> int:
        return self._archiver.close()

    def __enter__(self) -> MarketSnapshotRecorder:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


class MarketReplayReader:
    """Deterministic reader reconstructing PreFetchedBundle sequences from JSONL archives."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def iter_records(self) -> Iterator[dict[str, Any]]:
        if not self.path.is_file():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj

    def load_bundles(self) -> list[PreFetchedBundle]:
        bundles: list[PreFetchedBundle] = []
        for rec in self.iter_records():
            bundles.append(
                PreFetchedBundle(
                    snapshot_id=str(rec.get("snapshot_id") or "replay-snap"),
                    vst_status=dict(rec.get("vst_status") or {}),
                    contexts_by_symbol=dict(rec.get("contexts_by_symbol") or {}),
                    fetch_ms=float(rec.get("fetch_ms") or 0.0),
                    market_leader=rec.get("market_leader"),
                )
            )
        return bundles


__all__ = ["MarketReplayReader", "MarketSnapshotRecorder"]
