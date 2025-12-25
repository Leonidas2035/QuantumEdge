"""Snapshot scheduler for SupervisorAgent."""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from supervisor.audit_report import load_events_for_date
from supervisor.config import SnapshotSchedulerConfig
from supervisor.events import BaseEvent, EventLogger, EventType
from supervisor.llm.market_risk_monitor import MarketRiskMonitor
from supervisor.llm.trading_behavior_analyzer import TradingBehaviorAnalyzer
from supervisor.llm.trend_evaluator import TrendEvaluator
from supervisor.snapshot_models import SnapshotReport


class SnapshotScheduler:
    """Builds periodic Supervisor snapshots."""

    def __init__(
        self,
        config: SnapshotSchedulerConfig,
        events_dir: Path,
        event_logger: EventLogger,
        trend_evaluator: TrendEvaluator,
        market_risk_monitor: MarketRiskMonitor,
        behavior_analyzer: TradingBehaviorAnalyzer,
        state_path: Path,
        logger: logging.Logger,
    ) -> None:
        self._config = config
        self._events_dir = events_dir
        self._event_logger = event_logger
        self._trend = trend_evaluator
        self._risk = market_risk_monitor
        self._behavior = behavior_analyzer
        self._state_path = state_path
        self._logger = logger
        self._latest_snapshot = self._load_last_snapshot()

    @property
    def latest_snapshot(self) -> Optional[SnapshotReport]:
        return self._latest_snapshot

    def run_once(self) -> Optional[SnapshotReport]:
        if not self._config.enabled:
            return None

        events = self._load_recent_events()
        if not events:
            self._logger.info("Snapshot skipped: no recent events")
            return None

        market_slice = self._build_market_slice(events)
        risk_context = self._build_risk_context(events, market_slice)
        trade_history = self._extract_trade_history(events)
        signal_history = self._extract_signal_history(events)

        trend_result = self._trend.evaluate(market_slice)
        risk_result = self._risk.analyze(risk_context)
        behavior_result = self._behavior.analyze(trade_history, signal_history)
        aggregates = self._aggregate_performance(trade_history)

        snapshot = SnapshotReport(
            timestamp=datetime.now(timezone.utc),
            trend=trend_result.trend,
            trend_confidence=trend_result.confidence,
            market_risk_level=risk_result.risk_level,
            market_risk_triggers=risk_result.triggers,
            behavior_pnl_quality=behavior_result.pnl_quality,
            behavior_signal_quality=behavior_result.signal_quality,
            behavior_flags=behavior_result.behavior_flags,
            total_trades=aggregates["total_trades"],
            recent_winrate=aggregates["recent_winrate"],
            recent_drawdown_pct=aggregates["drawdown_pct"],
        )

        self._event_logger.log_supervisor_snapshot(snapshot)
        self._persist_latest(snapshot)
        self._logger.info(
            "Snapshot generated: trend=%s risk=%s pnl=%s",
            snapshot.trend,
            snapshot.market_risk_level,
            snapshot.behavior_pnl_quality,
        )
        return snapshot

    # Internals
    def _load_recent_events(self) -> List[BaseEvent]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=self._config.history_window_minutes)
        events: List[BaseEvent] = []
        for offset in (0, -1):
            day = (now + timedelta(days=offset)).date()
            if day < cutoff.date():
                continue
            events.extend(load_events_for_date(self._events_dir, day))
        events = [e for e in events if e.ts >= cutoff]
        events.sort(key=lambda e: e.ts)
        return events

    def _build_market_slice(self, events: List[BaseEvent]) -> Dict[str, Any]:
        decisions = [e for e in events if e.type == EventType.ORDER_DECISION]
        wins = losses = 0
        allowed = 0
        buy = sell = 0
        for dec in decisions:
            if bool(dec.data.get("allowed", True)):
                allowed += 1
            if (dec.data.get("side") or "").upper() == "BUY":
                buy += 1
            elif (dec.data.get("side") or "").upper() == "SELL":
                sell += 1
        orders = [e for e in events if e.type == EventType.ORDER_RESULT]
        pnl_series = []
        for trade in orders:
            pnl = trade.data.get("pnl")
            if isinstance(pnl, (int, float)):
                pnl_series.append(float(pnl))
            result = (trade.data.get("result") or "").upper()
            if result == "WIN":
                wins += 1
            elif result == "LOSS":
                losses += 1
        volatility_metric = statistics.pstdev(pnl_series) if len(pnl_series) > 1 else (abs(pnl_series[0]) if pnl_series else 0.0)
        total_decisions = len(decisions) or 1
        recent_winrate = wins / max(1, wins + losses)
        return {
            "wins": wins,
            "losses": losses,
            "recent_winrate": recent_winrate,
            "allowed_ratio": allowed / total_decisions,
            "net_side_bias": (buy - sell) / max(1, buy + sell),
            "volatility_metric": volatility_metric,
            "pnl_series": pnl_series,
        }

    def _build_risk_context(self, events: List[BaseEvent], market_slice: Dict[str, Any]) -> Dict[str, Any]:
        breach_count = sum(1 for e in events if e.type == EventType.RISK_LIMIT_BREACH)
        anomaly_count = sum(1 for e in events if e.type == EventType.ANOMALY)
        notes: List[str] = []
        if breach_count:
            notes.append("risk_breach")
        if anomaly_count:
            notes.append("anomaly")
        return {
            "volatility_metric": market_slice.get("volatility_metric", 0.0),
            "breach_count": breach_count,
            "anomaly_count": anomaly_count,
            "orderbook_imbalance": market_slice.get("net_side_bias", 0.0),
            "notes": notes,
        }

    def _extract_trade_history(self, events: List[BaseEvent]) -> List[Dict[str, Any]]:
        history: List[Dict[str, Any]] = []
        for event in events:
            if event.type == EventType.ORDER_RESULT:
                history.append(
                    {
                        "ts": event.ts.isoformat(),
                        "symbol": event.data.get("symbol"),
                        "result": event.data.get("result"),
                        "pnl": event.data.get("pnl"),
                    }
                )
        return history

    def _extract_signal_history(self, events: List[BaseEvent]) -> List[Dict[str, Any]]:
        history: List[Dict[str, Any]] = []
        for event in events:
            if event.type == EventType.ORDER_DECISION:
                history.append(
                    {
                        "ts": event.ts.isoformat(),
                        "symbol": event.data.get("symbol"),
                        "side": event.data.get("side"),
                        "allowed": event.data.get("allowed", True),
                        "code": event.data.get("code"),
                    }
                )
        return history

    def _aggregate_performance(self, trades: List[Dict[str, Any]]) -> Dict[str, float]:
        wins = sum(1 for t in trades if (t.get("result") or "").upper() == "WIN")
        losses = sum(1 for t in trades if (t.get("result") or "").upper() == "LOSS")
        winrate = wins / max(1, wins + losses)
        cumulative = 0.0
        peak = 0.0
        drawdown = 0.0
        for trade in trades:
            pnl = trade.get("pnl")
            if isinstance(pnl, (int, float)):
                cumulative += float(pnl)
                if cumulative > peak:
                    peak = cumulative
                drawdown = min(drawdown, cumulative - peak)
        drawdown_pct = abs(drawdown) / abs(peak) if peak else 0.0
        return {
            "total_trades": len(trades),
            "recent_winrate": winrate,
            "drawdown_pct": drawdown_pct,
        }

    def _persist_latest(self, snapshot: SnapshotReport) -> None:
        self._latest_snapshot = snapshot
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._state_path.open("w", encoding="utf-8") as handle:
            json.dump(snapshot.to_dict(), handle, indent=2)

    def _load_last_snapshot(self) -> Optional[SnapshotReport]:
        if not self._state_path.exists():
            return None
        try:
            with self._state_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return SnapshotReport.from_dict(data)
        except Exception:
            return None
