"""Adaptive ReasoningEffortRouter selecting thinking_level via DecisionComplexityScore + hysteresis."""

from __future__ import annotations

from typing import Any

from quantum_edge_core.contracts.optimization import ThinkingLevel
from quantum_edge_ml.model_router.backends.google_gemini import clamp_thinking_level


class ReasoningEffortRouter:
    """Routes Stage-2 thinking_level on the same frontier model using hysteresis-stabilized complexity."""

    WEIGHTS: dict[str, float] = {
        "signal_conflict": 0.25,
        "regime_entropy": 0.15,
        "vol_anomaly": 0.15,
        "exposure": 0.15,
        "liq_risk": 0.15,
        "forecast_disagreement": 0.15,
    }

    def __init__(self, model_id: str = "gemini-4-carbon", hysteresis: float = 0.05) -> None:
        self.model_id = model_id
        self.h = hysteresis
        self._last_level_by_sym: dict[str, ThinkingLevel] = {}

    def compute_complexity(
        self,
        *,
        ctx: dict[str, Any],
        forecast: dict[str, Any] | None,
        vst_status: dict[str, Any],
    ) -> float:
        micro = ctx.get("microstructure_and_liquidity") or {}
        comp_score = float((micro.get("composite_signal") or {}).get("score") or 0.0)
        of_imb = float((micro.get("orderflow") or {}).get("imbalance") or 0.0)
        signal_conflict = (
            1.0
            if (comp_score * of_imb < -5.0)
            else (0.4 if abs(comp_score) < 15.0 else 0.1)
        )

        regime_entropy = float((forecast or {}).get("regime_entropy", 0.5))
        vol_z = abs(float((forecast or {}).get("vol_zscore", 0.0)))
        vol_anomaly = min(1.0, vol_z / 3.0)

        open_cnt = int(vst_status.get("open_positions_count") or 0)
        exposure = min(1.0, open_cnt / 3.0)

        spread_bps = float((ctx.get("market_snapshot") or {}).get("spread_bps") or 0.0)
        liq_risk = min(1.0, spread_bps / 30.0)

        disagreement = float((forecast or {}).get("tsfm_disagreement", 0.0))

        feats = {
            "signal_conflict": signal_conflict,
            "regime_entropy": regime_entropy,
            "vol_anomaly": vol_anomaly,
            "exposure": exposure,
            "liq_risk": liq_risk,
            "forecast_disagreement": disagreement,
        }
        return round(
            sum(self.WEIGHTS[k] * max(0.0, min(1.0, feats[k])) for k in self.WEIGHTS),
            4,
        )

    def select_level_from_score(
        self,
        symbol: str,
        d_val: float,
        *,
        stage: int = 2,
    ) -> tuple[float, ThinkingLevel]:
        """Apply hysteresis thresholds (0.30 / 0.65 / 0.85 +/- h) and model caps to a score."""
        if stage == 1:
            return 0.0, ThinkingLevel.LOW
        sym = symbol.upper()
        score = round(float(max(0.0, min(1.0, d_val))), 4)
        prev = self._last_level_by_sym.get(sym, ThinkingLevel.MEDIUM)
        nxt = prev

        if prev == ThinkingLevel.LOW:
            if score >= 0.85 + self.h:
                nxt = ThinkingLevel.XHIGH
            elif score >= 0.65 + self.h:
                nxt = ThinkingLevel.HIGH
            elif score >= 0.30 + self.h:
                nxt = ThinkingLevel.MEDIUM
        elif prev == ThinkingLevel.MEDIUM:
            if score >= 0.85 + self.h:
                nxt = ThinkingLevel.XHIGH
            elif score >= 0.65 + self.h:
                nxt = ThinkingLevel.HIGH
            elif score < 0.30 - self.h:
                nxt = ThinkingLevel.LOW
        elif prev == ThinkingLevel.HIGH:
            if score >= 0.85 + self.h:
                nxt = ThinkingLevel.XHIGH
            elif score < 0.30 - self.h:
                nxt = ThinkingLevel.LOW
            elif score < 0.65 - self.h:
                nxt = ThinkingLevel.MEDIUM
        elif prev == ThinkingLevel.XHIGH:
            if score < 0.30 - self.h:
                nxt = ThinkingLevel.LOW
            elif score < 0.65 - self.h:
                nxt = ThinkingLevel.MEDIUM
            elif score < 0.85 - self.h:
                nxt = ThinkingLevel.HIGH

        nxt = clamp_thinking_level(self.model_id, nxt)
        self._last_level_by_sym[sym] = nxt
        return score, nxt

    def select_level(
        self,
        *,
        symbol: str,
        ctx: dict[str, Any],
        forecast: dict[str, Any] | None,
        vst_status: dict[str, Any],
        stage: int = 2,
    ) -> tuple[float, ThinkingLevel]:
        if stage == 1:
            return 0.0, ThinkingLevel.LOW
        d_val = self.compute_complexity(ctx=ctx, forecast=forecast, vst_status=vst_status)
        return self.select_level_from_score(symbol, d_val, stage=stage)


__all__ = ["ReasoningEffortRouter"]
