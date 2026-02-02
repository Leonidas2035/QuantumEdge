"""Market risk monitor backed by LLM with safe fallback."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from supervisor.config import MarketRiskMonitorConfig
from supervisor.llm.chat_client import ChatCompletionsClient
from supervisor.utils.rate_limit import PerMinuteRateLimiter

RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass
class MarketRiskResult:
    risk_level: RiskLevel
    triggers: List[str]
    comment: str
    evaluated_at: datetime


class MarketRiskMonitor:
    """Evaluates current market risk level."""

    def __init__(
        self,
        config: MarketRiskMonitorConfig,
        llm_client: Optional[ChatCompletionsClient],
        logger: logging.Logger,
    ) -> None:
        self._config = config
        self._llm = llm_client
        self._logger = logger
        self._rate_limiter = PerMinuteRateLimiter(config.max_calls_per_minute)

    def analyze(self, risk_context: Dict[str, Any]) -> MarketRiskResult:
        timestamp = datetime.now(timezone.utc)
        if not risk_context:
            return MarketRiskResult("LOW", ["insufficient data"], "No risk context", timestamp)

        if not self._config.enabled or not self._llm:
            return self._heuristic(risk_context, timestamp, "LLM disabled")

        if not self._rate_limiter.allow():
            return self._heuristic(risk_context, timestamp, "rate limited")

        prompt = self._build_prompt(risk_context)
        try:
            response = self._llm.complete(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": "You classify market risk. Respond JSON {\"risk_level\":LOW/MEDIUM/HIGH,\"triggers\":[],\"comment\":...}."},
                    {"role": "user", "content": prompt},
                ],
                temperature=self._config.temperature,
                timeout_seconds=self._config.timeout_seconds,
            )
            return self._parse_response(response, timestamp)
        except Exception as exc:  # pragma: no cover - network/LLM errors
            self._logger.warning("Market risk monitor fallback: %s", exc)
            return self._heuristic(risk_context, timestamp, f"LLM error: {exc}")

    def _build_prompt(self, context: Dict[str, Any]) -> str:
        return (
            "Classify the market risk based on:\n"
            f"- Volatility metric: {context.get('volatility_metric')}\n"
            f"- Risk breaches: {context.get('breach_count')}\n"
            f"- Orderbook imbalance: {context.get('orderbook_imbalance')}\n"
            f"- Anomaly count: {context.get('anomaly_count')}\n"
            f"- Notes: {', '.join(context.get('notes', []))}\n"
            "Return JSON with risk_level LOW/MEDIUM/HIGH, triggers list, comment."
        )

    def _heuristic(self, context: Dict[str, Any], timestamp: datetime, reason: str) -> MarketRiskResult:
        volatility = abs(context.get("volatility_metric") or 0.0)
        breaches = int(context.get("breach_count") or 0)
        imbalance = abs(context.get("orderbook_imbalance") or 0.0)
        level: RiskLevel = "LOW"
        triggers: List[str] = []

        if volatility > 1.5 or breaches > 0 or imbalance > 0.5:
            level = "HIGH"
            if volatility > 1.5:
                triggers.append("volatility")
            if breaches > 0:
                triggers.append("risk_breaches")
            if imbalance > 0.5:
                triggers.append("orderbook_imbalance")
        elif volatility > 0.8 or imbalance > 0.2:
            level = "MEDIUM"
            if volatility > 0.8:
                triggers.append("volatility")
            if imbalance > 0.2:
                triggers.append("orderbook_imbalance")
        else:
            triggers.append("calm")

        comment = f"Heuristic risk ({reason})"
        return MarketRiskResult(level, triggers, comment, timestamp)

    def _parse_response(self, raw: str, timestamp: datetime) -> MarketRiskResult:
        payload = json.loads(raw.strip())
        level = str(payload.get("risk_level", "LOW")).upper()
        if level not in {"LOW", "MEDIUM", "HIGH"}:
            level = "LOW"
        triggers_raw = payload.get("triggers") or []
        triggers = [str(t) for t in triggers_raw if isinstance(t, str)]
        comment = str(payload.get("comment") or "LLM risk assessment")
        return MarketRiskResult(level, triggers, comment, timestamp)
