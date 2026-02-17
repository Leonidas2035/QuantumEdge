"""Cache for latest microstructure features per symbol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class MicrostructureSnapshot:
    ts_ms: int
    values: Dict[str, float]


class MicrostructureCache:
    def __init__(self, max_age_ms: int = 2000) -> None:
        self._max_age_ms = max(int(max_age_ms), 0)
        self._cache: Dict[str, MicrostructureSnapshot] = {}

    def update_from_event(self, event: Dict[str, Any]) -> None:
        symbol = str(event.get("s") or "")
        if not symbol:
            return
        ts_raw = (
            event.get("ts_event")
            or event.get("ts_ingest")
            or event.get("E")
            or event.get("T")
        )
        try:
            ts_ms = (
                int(ts_raw) // 1_000_000
                if ts_raw and int(ts_raw) > 1_000_000_000_000
                else int(ts_raw or 0)
            )
        except Exception:
            ts_ms = 0
        values = {
            "ofi_z": _safe_float(event.get("ofi_z")),
            "ofi_ma5": _safe_float(event.get("ofi_ma5")),
            "spread_bps": _safe_float(event.get("spread_bps")),
            "top_qty_sum": _safe_float(event.get("top_qty_sum")),
            "trade_rate_1s": _safe_float(event.get("trade_rate_1s")),
            "volume_1s": _safe_float(event.get("volume_1s")),
        }
        self._cache[symbol] = MicrostructureSnapshot(ts_ms=ts_ms, values=values)

    def latest(self, symbol: str, now_ms: int) -> Optional[Dict[str, float]]:
        snapshot = self._cache.get(symbol)
        if not snapshot:
            return None
        if (
            self._max_age_ms
            and snapshot.ts_ms
            and now_ms - snapshot.ts_ms > self._max_age_ms
        ):
            return None
        return dict(snapshot.values)


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except Exception:
        return 0.0
