"""Helpers to inspect the L2 spool state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SpoolSummary:
    bytes: int
    files: int
    oldest: Optional[Path]
    newest: Optional[Path]


def summarize_spool(root: Path) -> SpoolSummary:
    if not root.exists():
        return SpoolSummary(bytes=0, files=0, oldest=None, newest=None)
    files = [path for path in root.rglob("*.jsonl.gz") if path.is_file()]
    if not files:
        return SpoolSummary(bytes=0, files=0, oldest=None, newest=None)
    total_bytes = sum(path.stat().st_size for path in files)
    oldest = min(files, key=lambda path: path.stat().st_mtime)
    newest = max(files, key=lambda path: path.stat().st_mtime)
    return SpoolSummary(bytes=total_bytes, files=len(files), oldest=oldest, newest=newest)
