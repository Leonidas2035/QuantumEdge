"""Lightweight drift monitor for feature statistics."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class DriftSnapshot:
    drift_score: float
    exceed_rate: float
    top_features: list[str]


@dataclass
class DriftMonitor:
    baseline_mean: Dict[str, float]
    baseline_std: Dict[str, float]
    window: int = 300
    z_threshold: float = 3.0
    _z_scores: deque = field(default_factory=lambda: deque(maxlen=300))
    _feature_exceed: Dict[str, int] = field(default_factory=dict)
    _count: int = 0

    def update(
        self, feature_names: list[str], values: list[float]
    ) -> Optional[DriftSnapshot]:
        if not self.baseline_mean or not self.baseline_std:
            return None
        z_total = 0.0
        exceeds = 0
        for name, value in zip(feature_names, values):
            std = float(self.baseline_std.get(name, 0.0) or 0.0)
            if std <= 0:
                continue
            mean = float(self.baseline_mean.get(name, 0.0) or 0.0)
            z = abs((float(value) - mean) / std)
            z_total += z
            if z >= self.z_threshold:
                exceeds += 1
                self._feature_exceed[name] = self._feature_exceed.get(name, 0) + 1
        if feature_names:
            z_avg = z_total / max(len(feature_names), 1)
        else:
            z_avg = 0.0
        self._z_scores.append(z_avg)
        self._count += 1
        if self._count < 5:
            return None
        return self.snapshot()

    def snapshot(self) -> DriftSnapshot:
        if not self._z_scores:
            return DriftSnapshot(drift_score=0.0, exceed_rate=0.0, top_features=[])
        drift_score = sum(self._z_scores) / max(len(self._z_scores), 1)
        total_exceeds = sum(self._feature_exceed.values())
        exceed_rate = total_exceeds / max(self._count, 1)
        top_features = sorted(
            self._feature_exceed, key=self._feature_exceed.get, reverse=True
        )[:5]
        return DriftSnapshot(
            drift_score=float(drift_score),
            exceed_rate=float(exceed_rate),
            top_features=top_features,
        )
