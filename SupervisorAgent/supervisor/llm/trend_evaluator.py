"""Trend evaluator using LLM with graceful fallbacks."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from supervisor.config import TrendEvaluatorConfig
from supervisor.llm.chat_client import ChatCompletionsClient
from supervisor.utils.cache import TtlCache
from supervisor.utils.rate_limit import PerMinuteRateLimiter

TrendLabel = Literal["UP", "DOWN", "RANGE", "UNKNOWN"]


@dataclass
class TrendResult:
    trend: TrendLabel
    confidence: float
    comment: str
    evaluated_at: datetime


class TrendEvaluator:
    """Computes mid-term trend labels using an LLM or heuristics."""

    def __init__(
        self,
        config: TrendEvaluatorConfig,
        llm_client: Optional[ChatCompletionsClient],
        logger: logging.Logger,
    ) -> None:
        self._config = config
        self._llm = llm_client
        self._logger = logger
        self._rate_limiter = PerMinuteRateLimiter(config.max_calls_per_minute)
        self._cache = TtlCache(config.cache_ttl_seconds) if config.cache_enabled else None

    def evaluate(self, market_slice: Dict[str, Any]) -> TrendResult:
        timestamp = datetime.now(timezone.utc)
        if not market_slice:
            return TrendResult("UNKNOWN", 0.0, "insufficient data", timestamp)

        cache_key = json.dumps(market_slice, sort_keys=True) if self._cache else None
        if cache_key and self._cache:
            cached = self._cache.get(cache_key)
            if isinstance(cached, TrendResult):
                return cached

        if not self._config.enabled or not self._llm:
            result = self._heuristic(market_slice, timestamp, "LLM disabled")
            if cache_key and self._cache:
                self._cache.set(cache_key, result)
            return result

        if not self._rate_limiter.allow():
            return self._heuristic(market_slice, timestamp, "rate limited")

        prompt = self._build_prompt(market_slice)
        try:
            response = self._llm.complete(
                model=self._config.model,
                messages=
                [
                    {"role": "system", "content": "You are a trading trend analyst. Respond with JSON {\"trend\":...,\"confidence\":0..1,\"comment\":...}."},
                    {"role": "user", "content": prompt},
                ],
                temperature=self._config.temperature,
                timeout_seconds=self._config.timeout_seconds,
            )
            result = self._parse_response(response, timestamp)
        except Exception as exc:  # pragma: no cover - network/LLM errors
            self._logger.warning("Trend evaluator fallback due to error: %s", exc)
            result = self._heuristic(market_slice, timestamp, f"LLM error: {exc}")

        if cache_key and self._cache:
            self._cache.set(cache_key, result)
        return result

    def _build_prompt(self, market_slice: Dict[str, Any]) -> str:
        return (
            "Summarize the probable market trend based on the following metrics:\n"
            f"- Wins: {market_slice.get('wins')}\n"
            f"- Losses: {market_slice.get('losses')}\n"
            f"- Recent winrate: {market_slice.get('recent_winrate')}\n"
            f"- Allowed ratio: {market_slice.get('allowed_ratio')}\n"
            f"- Net position bias: {market_slice.get('net_side_bias')}\n"
            f"- Volatility metric: {market_slice.get('volatility_metric')}\n"
            "Return JSON with keys trend (UP/DOWN/RANGE), confidence (0..1), comment."
        )

    def _heuristic(self, market_slice: Dict[str, Any], timestamp: datetime, reason: str) -> TrendResult:
        winrate = market_slice.get("recent_winrate") or 0.0
        pnl_series = market_slice.get("pnl_series") or []
        net_bias = market_slice.get("net_side_bias") or 0.0

        trend: TrendLabel = "RANGE"
        if winrate > 0.6 or net_bias > 0.2:
            trend = "UP"
        elif winrate < 0.4 or net_bias < -0.2:
            trend = "DOWN"
        elif not pnl_series:
            trend = "UNKNOWN"

        confidence = min(0.9, max(0.1, abs(winrate - 0.5) * 2))
        comment = f"Heuristic trend ({reason})"
        return TrendResult(trend, confidence, comment, timestamp)

    def _parse_response(self, raw: str, timestamp: datetime) -> TrendResult:
        payload = json.loads(raw.strip())
        trend_raw = str(payload.get("trend", "UNKNOWN")).upper()
        if trend_raw not in {"UP", "DOWN", "RANGE", "UNKNOWN"}:
            trend_raw = "UNKNOWN"
        confidence = float(payload.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        comment = str(payload.get("comment") or "LLM trend assessment")
        return TrendResult(trend_raw, confidence, comment, timestamp)
