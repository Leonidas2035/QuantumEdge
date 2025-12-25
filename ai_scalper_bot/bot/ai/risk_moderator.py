import asyncio
import hashlib
import json
import time
from typing import Any, Dict, Tuple

from bot.core.config_loader import config


def _hash_features(features) -> str:
    try:
        rounded = [round(float(x), 6) for x in features]
    except Exception:
        rounded = []
    payload = json.dumps(rounded, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class LLMRiskModerator:
    """
    Deterministic risk guardrail intended to be backed by GPT-5.1.
    For offline/dev environments, uses heuristics + caching and keeps the same interface.
    """

    def __init__(self, cache_ttl: float = 5.0, min_interval: float = 0.05):
        app_risk = config.get("app.risk", {}) or {}
        self.require_edge = app_risk.get("llm_require_edge", 0.015)
        self.max_dd = app_risk.get("max_daily_dd", 0.03)
        self.max_exposure = app_risk.get("max_exposure", 2.0)
        self.cache_ttl = cache_ttl
        self.min_interval = min_interval
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._last_call = 0.0
        self.prompt_template = (
            "You are a risk moderator for a high-frequency crypto scalper. "
            "Respond ONLY with a compact JSON object: "
            '{"approve":bool,"risk_score":0-1,"reason":string,"leverage":float}. '
            "Consider: microstructure features, signal probabilities (p_up, p_down, edge, direction), "
            "position state, volatility regime, trend classification, volume/imbalance, "
            "risk context (max_dd, exposure, trade frequency). "
            "Reject trades with low edge, pump/dumps, or when drawdown/exposure thresholds are breached. "
            "Be deterministic and concise."
        )

    async def evaluate(self, features, signal, market_context) -> Dict[str, Any]:
        """
        Returns {
            approve: bool,
            risk_score: float (0-1),
            reason: str
        }
        """
        key = _hash_features(features) + f":{signal.direction}"
        now = time.time()

        cached = self._cache.get(key)
        if cached and now - cached[0] <= self.cache_ttl:
            return cached[1]

        if now - self._last_call < self.min_interval:
            await asyncio.sleep(self.min_interval - (now - self._last_call))
        self._last_call = time.time()

        result = self._heuristic_eval(features, signal, market_context)
        self._cache[key] = (time.time(), result)
        return result

    def _build_prompt(self, features, signal, market_context) -> str:
        return (
            f"{self.prompt_template}\n"
            f"features_tail={list(features[-10:]) if features is not None else []}\n"
            f"signal={{'p_up':{signal.p_up},'p_down':{signal.p_down},'edge':{signal.edge},'direction':{signal.direction}}}\n"
            f"market_context={market_context}"
        )

    def _heuristic_eval(self, features, signal, market_context) -> Dict[str, Any]:
        edge = getattr(signal, "edge", 0.0)
        vol = 0.0
        try:
            # ret_std_10 is the 8th feature in FEATURE_COLS
            vol = abs(float(features[7]))
        except Exception:
            vol = 0.0

        drawdown = market_context.get("drawdown", 0.0)
        exposure = market_context.get("exposure", 0.0)
        shock = market_context.get("shock", 0.0)

        risk_score = max(0.0, min(1.0, 0.5 + edge * 5 - drawdown * 5 - shock * 2 - vol * 2))
        approve = (
            edge >= self.require_edge
            and drawdown <= self.max_dd
            and exposure <= self.max_exposure
            and shock < 0.05
            and vol < 0.05
        )

        reason_parts = []
        if edge < self.require_edge:
            reason_parts.append("edge too small")
        if drawdown > self.max_dd:
            reason_parts.append("drawdown limit exceeded")
        if exposure > self.max_exposure:
            reason_parts.append("exposure limit exceeded")
        if shock >= 0.05:
            reason_parts.append("price shock detected")
        if vol >= 0.05:
            reason_parts.append("elevated volatility")

        reason = "; ".join(reason_parts) if reason_parts else "approved"

        return {
            "approve": approve,
            "risk_score": float(round(risk_score, 4)),
            "reason": reason,
        }
