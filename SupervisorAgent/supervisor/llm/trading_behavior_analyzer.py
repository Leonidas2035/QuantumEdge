"""Trading behavior analyzer for Supervisor snapshots."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from supervisor.config import TradingBehaviorConfig
from supervisor.llm.chat_client import ChatCompletionsClient
from supervisor.utils.rate_limit import PerMinuteRateLimiter


@dataclass
class BehaviorResult:
    pnl_quality: str
    signal_quality: str
    behavior_flags: List[str]
    comment: str
    evaluated_at: datetime


class TradingBehaviorAnalyzer:
    """Assesses bot behavior with optional LLM reasoning."""

    def __init__(
        self,
        config: TradingBehaviorConfig,
        llm_client: Optional[ChatCompletionsClient],
        logger: logging.Logger,
    ) -> None:
        self._config = config
        self._llm = llm_client
        self._logger = logger
        self._rate_limiter = PerMinuteRateLimiter(config.max_calls_per_minute)

    def analyze(self, trade_history: List[Dict[str, Any]], signal_history: List[Dict[str, Any]]) -> BehaviorResult:
        timestamp = datetime.now(timezone.utc)
        if not trade_history and not signal_history:
            return BehaviorResult("UNKNOWN", "UNKNOWN", ["NO_DATA"], "Insufficient history", timestamp)

        if not self._config.enabled or not self._llm:
            return self._heuristic(trade_history, signal_history, timestamp, "LLM disabled")

        if not self._rate_limiter.allow():
            return self._heuristic(trade_history, signal_history, timestamp, "rate limited")

        prompt = self._build_prompt(trade_history, signal_history)
        try:
            response = self._llm.complete(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": "Assess trading behavior. Respond JSON {\"pnl_quality\":,\"signal_quality\":,\"behavior_flags\":[],\"comment\":...}."},
                    {"role": "user", "content": prompt},
                ],
                temperature=self._config.temperature,
                timeout_seconds=self._config.timeout_seconds,
            )
            return self._parse_response(response, timestamp)
        except Exception as exc:  # pragma: no cover - network/LLM errors
            self._logger.warning("Trading behavior analyzer fallback: %s", exc)
            return self._heuristic(trade_history, signal_history, timestamp, f"LLM error: {exc}")

    def _build_prompt(self, trades: List[Dict[str, Any]], signals: List[Dict[str, Any]]) -> str:
        last_trades = trades[-self._config.history_trades :]
        last_signals = signals[-self._config.history_signals :]
        pnl_samples = [t.get("pnl") for t in last_trades if isinstance(t.get("pnl"), (int, float))]
        win_count = sum(1 for t in last_trades if (t.get("result") or "").upper() == "WIN")
        loss_count = sum(1 for t in last_trades if (t.get("result") or "").upper() == "LOSS")
        winrate = win_count / max(1, (win_count + loss_count))
        signal_bias = sum(1 if s.get("side") == "BUY" else -1 for s in last_signals)
        return (
            "Evaluate the bot's recent behavior.\n"
            f"- Trades analyzed: {len(last_trades)}\n"
            f"- Signals analyzed: {len(last_signals)}\n"
            f"- Winrate: {winrate:.2f}\n"
            f"- Avg PnL: {sum(pnl_samples)/len(pnl_samples) if pnl_samples else 0:.4f}\n"
            f"- Signal bias: {signal_bias}\n"
            "Provide concise behavior flags if any issues like overtrading, revenge trading, or tilt are suspected."
        )

    def _heuristic(
        self,
        trades: List[Dict[str, Any]],
        signals: List[Dict[str, Any]],
        timestamp: datetime,
        reason: str,
    ) -> BehaviorResult:
        wins = sum(1 for t in trades if (t.get("result") or "").upper() == "WIN")
        losses = sum(1 for t in trades if (t.get("result") or "").upper() == "LOSS")
        winrate = wins / max(1, wins + losses)
        pnl_quality = "NEUTRAL"
        if winrate > 0.6:
            pnl_quality = "GOOD"
        elif winrate < 0.4:
            pnl_quality = "BAD"

        signal_quality = "NORMAL"
        if not signals:
            signal_quality = "UNKNOWN"
        else:
            allowed = sum(1 for s in signals if bool(s.get("allowed", True)))
            ratio = allowed / max(1, len(signals))
            if ratio < 0.4:
                signal_quality = "WEAK"
            elif ratio > 0.8:
                signal_quality = "STRONG"

        flags: List[str] = []
        if len(trades) > 0 and len(trades) > len(signals) * 1.5:
            flags.append("OVERTRADING")
        if pnl_quality == "BAD" and len(trades) > 10:
            flags.append("POSSIBLE_TILT")

        comment = f"Heuristic behavior assessment ({reason})"
        return BehaviorResult(pnl_quality, signal_quality, flags or ["NORMAL"], comment, timestamp)

    def _parse_response(self, raw: str, timestamp: datetime) -> BehaviorResult:
        payload = json.loads(raw.strip())
        pnl_quality = str(payload.get("pnl_quality", "UNKNOWN")).upper()
        signal_quality = str(payload.get("signal_quality", "UNKNOWN")).upper()
        flags = payload.get("behavior_flags") or []
        flag_list = [str(f) for f in flags if isinstance(f, str)]
        comment = str(payload.get("comment") or "LLM behavior assessment")
        return BehaviorResult(pnl_quality, signal_quality, flag_list or ["NORMAL"], comment, timestamp)
