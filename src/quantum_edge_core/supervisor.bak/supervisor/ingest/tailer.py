"""File tailer for JSONL ingestion with offset tracking."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class TailResult:
    lines: List[str]
    offset: int
    reset: bool
    dropped_lines: int


class FileTailer:
    def __init__(self, path: Path, max_line_kb: int = 256) -> None:
        self.path = Path(path)
        self.max_line_bytes = max(int(max_line_kb), 1) * 1024

    def read_new_lines(self, offset: int) -> TailResult:
        if not self.path.exists():
            return TailResult(lines=[], offset=offset, reset=False, dropped_lines=0)

        try:
            size = self.path.stat().st_size
        except OSError:
            return TailResult(lines=[], offset=offset, reset=False, dropped_lines=0)

        reset = False
        if offset > size:
            offset = 0
            reset = True

        lines: List[str] = []
        dropped = 0
        try:
            with self.path.open("rb") as handle:
                handle.seek(offset)
                while True:
                    raw = handle.readline()
                    if not raw:
                        break
                    if len(raw) > self.max_line_bytes:
                        dropped += 1
                        continue
                    try:
                        line = raw.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        dropped += 1
                        continue
                    if line:
                        lines.append(line)
                offset = handle.tell()
        except OSError:
            return TailResult(
                lines=[], offset=offset, reset=reset, dropped_lines=dropped
            )

        return TailResult(
            lines=lines, offset=offset, reset=reset, dropped_lines=dropped
        )
