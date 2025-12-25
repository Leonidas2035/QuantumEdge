"""Audit report utilities for SupervisorAgent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List

from supervisor.config import RiskConfig
from supervisor.events import BaseEvent, EventType


@dataclass
class AuditStats:
    """Aggregated statistics for a trading day."""

    date: date
    total_order_decisions: int = 0
    allowed_orders: int = 0
    denied_orders: int = 0
    denied_by_code: Dict[str, int] = field(default_factory=dict)
    halt_events: int = 0
    bot_starts: int = 0
    bot_stops: int = 0
    anomalies: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0


def load_events_for_date(events_dir: Path, target_date: date) -> List[BaseEvent]:
    """Load JSONL events for the given date."""

    filename = f"events_{target_date.isoformat()}.jsonl"
    path = events_dir / filename
    if not path.exists():
        return []

    events: List[BaseEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                ts = datetime.fromisoformat(raw["ts"])
                event = BaseEvent(
                    ts=ts,
                    type=EventType(raw["type"]),
                    source=raw.get("source", "unknown"),
                    data=raw.get("data", {}),
                )
                events.append(event)
            except Exception:
                continue
    return events


def compute_stats(events: Iterable[BaseEvent]) -> AuditStats:
    """Compute aggregated statistics from events."""

    stats = AuditStats(date=date.today())
    for event in events:
        stats.date = event.ts.date()
        if event.type == EventType.ORDER_DECISION:
            stats.total_order_decisions += 1
            allowed = bool(event.data.get("allowed"))
            if allowed:
                stats.allowed_orders += 1
            else:
                stats.denied_orders += 1
                code = event.data.get("code", "UNKNOWN")
                stats.denied_by_code[code] = stats.denied_by_code.get(code, 0) + 1
        elif event.type == EventType.RISK_LIMIT_BREACH:
            stats.halt_events += 1
        elif event.type == EventType.BOT_START:
            stats.bot_starts += 1
        elif event.type == EventType.BOT_STOP:
            stats.bot_stops += 1
        elif event.type == EventType.ANOMALY:
            stats.anomalies += 1
        elif event.type == EventType.ORDER_RESULT:
            result = (event.data.get("result") or "").upper()
            if result == "WIN":
                stats.wins += 1
            elif result == "LOSS":
                stats.losses += 1
            elif result == "BREAKEVEN":
                stats.breakeven += 1
    return stats


def render_markdown_report(stats: AuditStats, limits: RiskConfig) -> str:
    """Render a human-readable markdown report."""

    winrate = None
    total_results = stats.wins + stats.losses
    if total_results > 0:
        winrate = stats.wins / total_results

    lines = [
        f"# Supervisor Audit Report — {stats.date.isoformat()}",
        "",
        "## Summary",
        f"- Total order decisions: {stats.total_order_decisions}",
        f"- Allowed: {stats.allowed_orders}",
        f"- Denied: {stats.denied_orders}",
        f"- Halt events: {stats.halt_events}",
        f"- Bot starts: {stats.bot_starts}",
        f"- Bot stops: {stats.bot_stops}",
        f"- Anomalies: {stats.anomalies}",
    ]

    if stats.denied_by_code:
        lines.append("- Deny reasons:")
        for code, count in sorted(stats.denied_by_code.items()):
            lines.append(f"  - {code}: {count}")

    lines.append("")
    lines.append("## Performance")
    lines.append(f"- Wins: {stats.wins}")
    lines.append(f"- Losses: {stats.losses}")
    lines.append(f"- Breakeven: {stats.breakeven}")
    if winrate is not None:
        lines.append(f"- Winrate: {winrate:.2%}")

    lines.extend(
        [
            "",
            "## Risk Limits (context)",
            f"- Currency: {limits.currency}",
            f"- Max daily loss (abs): {limits.max_daily_loss_abs}",
            f"- Max daily loss (pct): {limits.max_daily_loss_pct if limits.max_daily_loss_pct is not None else 'n/a'}",
            f"- Max drawdown (abs): {limits.max_drawdown_abs if limits.max_drawdown_abs is not None else 'n/a'}",
            f"- Max drawdown (pct): {limits.max_drawdown_pct if limits.max_drawdown_pct is not None else 'n/a'}",
            f"- Max notional per symbol: {limits.max_notional_per_symbol}",
            f"- Max leverage: {limits.max_leverage}",
        ]
    )

    return "\n".join(lines) + "\n"
