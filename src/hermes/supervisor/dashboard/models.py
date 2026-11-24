"""Lightweight datamodels for dashboard JSON responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Literal, Optional


@dataclass
class OverviewData:
    timestamp: datetime
    total_pnl: float = 0.0
    pnl_1h: float = 0.0
    open_positions: int = 0
    open_orders: int = 0
    strategy_mode: Optional[str] = None
    market_trend: Optional[str] = None
    market_risk_level: Optional[str] = None


@dataclass
class HealthStatus:
    status: Literal["OK", "WARN", "FAIL"]
    issues: List[str] = field(default_factory=list)
    last_heartbeat_at: Optional[datetime] = None
    last_snapshot_at: Optional[datetime] = None


@dataclass
class DashboardEvent:
    timestamp: datetime
    event_type: str
    symbol: Optional[str]
    details: dict[str, Any]
