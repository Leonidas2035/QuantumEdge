"""TSDB base abstractions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Protocol


@dataclass
class Point:
    """A single timeseries point."""

    measurement: str
    ts: datetime
    tags: Dict[str, str]
    fields: Dict[str, object]


class TimeseriesStore(Protocol):
    """Protocol for TSDB backends."""

    def write_points(self, points: list[Point]) -> None:
        ...

    def flush(self) -> None:
        ...
