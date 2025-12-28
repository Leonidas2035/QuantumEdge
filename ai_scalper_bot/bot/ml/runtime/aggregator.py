"""Multi-horizon gating policy for ML signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from bot.engine.decision_types import DecisionDirection
from bot.ml.ensemble import EnsembleOutput
from bot.ml.signal_model.model import SignalOutput


@dataclass
class GateResult:
    allow: bool
    direction: str
    confidence: float
    reasons: List[str]
    raw: Dict[int, Dict[str, float]]
    policy: str


def build_ensemble_output(outputs: Dict[int, SignalOutput], weights: Dict[int, float]) -> EnsembleOutput:
    if not outputs:
        return EnsembleOutput(meta_edge=0.0, direction=DecisionDirection.FLAT, components={})
    total_weight = sum(weights.get(h, 1.0) for h in outputs) or 1.0
    meta_edge = 0.0
    for h, sig in outputs.items():
        w = weights.get(h, 1.0) / total_weight
        meta_edge += sig.edge * w
    direction = DecisionDirection.LONG if meta_edge > 0 else (DecisionDirection.SHORT if meta_edge < 0 else DecisionDirection.FLAT)
    return EnsembleOutput(meta_edge=meta_edge, direction=direction, components=outputs)


class MultiHorizonAggregator:
    def __init__(
        self,
        policy: str,
        thresholds: Dict[int, float],
        weights: Optional[Dict[int, float]] = None,
        score_threshold: float = 0.0,
        min_gap: float = 0.0,
        cooldown_ms: int = 0,
    ):
        self.policy = policy
        self.thresholds = thresholds
        self.weights = weights or {h: 1.0 for h in thresholds}
        self.score_threshold = float(score_threshold)
        self.min_gap = float(min_gap)
        self.cooldown_ms = int(cooldown_ms)

    def evaluate(
        self,
        outputs: Dict[int, SignalOutput],
        direction: str,
        now_ms: int,
        last_trade_ms: Optional[int] = None,
    ) -> GateResult:
        reasons: List[str] = []
        raw = {h: {"p_up": sig.p_up, "p_down": sig.p_down} for h, sig in outputs.items()}

        if direction not in {DecisionDirection.LONG, DecisionDirection.SHORT}:
            reasons.append("NO_DIRECTION")
            return GateResult(False, direction, 0.0, reasons, raw, self.policy)

        if self.cooldown_ms and last_trade_ms and now_ms - last_trade_ms < self.cooldown_ms:
            reasons.append("COOLDOWN_ACTIVE")
            return GateResult(False, direction, 0.0, reasons, raw, self.policy)

        if not outputs:
            reasons.append("MODEL_MISSING")
            return GateResult(False, direction, 0.0, reasons, raw, self.policy)
        missing = [h for h in self.thresholds.keys() if h not in outputs]
        if missing:
            for h in missing:
                reasons.append(f"MODEL_MISSING_H{h}")
            return GateResult(False, direction, 0.0, reasons, raw, self.policy)

        if self.min_gap > 0:
            for h, sig in outputs.items():
                gap = sig.p_up - sig.p_down if direction == DecisionDirection.LONG else sig.p_down - sig.p_up
                if gap < self.min_gap:
                    reasons.append("ML_CONFIDENCE_GAP")
                    return GateResult(False, direction, gap, reasons, raw, self.policy)

        if self.policy == "weighted":
            score = 0.0
            total_weight = sum(self.weights.values()) or 1.0
            for h, sig in outputs.items():
                w = self.weights.get(h, 1.0) / total_weight
                value = sig.p_up - 0.5 if direction == DecisionDirection.LONG else sig.p_down - 0.5
                score += w * value
            if score < self.score_threshold:
                reasons.append("ML_SCORE_BELOW")
                return GateResult(False, direction, score, reasons, raw, self.policy)
            return GateResult(True, direction, score, reasons, raw, self.policy)

        if self.policy == "two_stage":
            if 1 not in outputs:
                reasons.append("MODEL_MISSING_H1")
                return GateResult(False, direction, 0.0, reasons, raw, self.policy)
            if not _passes_threshold(outputs[1], direction, self.thresholds.get(1, 0.0)):
                reasons.append("ML_THRESHOLD_FAIL_H1")
                return GateResult(False, direction, 0.0, reasons, raw, self.policy)

        for h, sig in outputs.items():
            threshold = float(self.thresholds.get(h, 0.0))
            if not _passes_threshold(sig, direction, threshold):
                reasons.append(f"ML_THRESHOLD_FAIL_H{h}")
                return GateResult(False, direction, threshold, reasons, raw, self.policy)

        confidence = min(sig.p_up if direction == DecisionDirection.LONG else sig.p_down for sig in outputs.values())
        return GateResult(True, direction, confidence, reasons, raw, self.policy)


def _passes_threshold(sig: SignalOutput, direction: str, threshold: float) -> bool:
    if direction == DecisionDirection.LONG:
        return sig.p_up >= threshold
    if direction == DecisionDirection.SHORT:
        return sig.p_down >= threshold
    return False
