"""Snapshot report datamodels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List


@dataclass
class SnapshotReport:
    """Aggregate supervisor snapshot."""

    timestamp: datetime
    trend: str
    trend_confidence: float
    market_risk_level: str
    market_risk_triggers: List[str]
    behavior_pnl_quality: str
    behavior_signal_quality: str
    behavior_flags: List[str]
    total_trades: int
    recent_winrate: float
    recent_drawdown_pct: float

    def to_dict(self) -> Dict[str, object]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "trend": self.trend,
            "trend_confidence": self.trend_confidence,
            "market_risk_level": self.market_risk_level,
            "market_risk_triggers": list(self.market_risk_triggers),
            "behavior_pnl_quality": self.behavior_pnl_quality,
            "behavior_signal_quality": self.behavior_signal_quality,
            "behavior_flags": list(self.behavior_flags),
            "total_trades": self.total_trades,
            "recent_winrate": self.recent_winrate,
            "recent_drawdown_pct": self.recent_drawdown_pct,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "SnapshotReport":
        timestamp_raw = data.get("timestamp")
        timestamp = datetime.fromisoformat(str(timestamp_raw)) if timestamp_raw else datetime.utcnow()
        return cls(
            timestamp=timestamp,
            trend=str(data.get("trend", "UNKNOWN")),
            trend_confidence=float(data.get("trend_confidence", 0.0)),
            market_risk_level=str(data.get("market_risk_level", "LOW")),
            market_risk_triggers=[str(x) for x in data.get("market_risk_triggers", [])],
            behavior_pnl_quality=str(data.get("behavior_pnl_quality", "UNKNOWN")),
            behavior_signal_quality=str(data.get("behavior_signal_quality", "UNKNOWN")),
            behavior_flags=[str(x) for x in data.get("behavior_flags", [])],
            total_trades=int(data.get("total_trades", 0)),
            recent_winrate=float(data.get("recent_winrate", 0.0)),
            recent_drawdown_pct=float(data.get("recent_drawdown_pct", 0.0)),
        )
