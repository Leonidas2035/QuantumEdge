"""Scenario definitions for offline research runs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    description: str
    start_ratio: float
    duration_ratio: float
    spread_factor: float = 1.0
    latency_ms: int = 0
    volatility_factor: float = 0.0


SCENARIOS = {
    "spread_spike": ScenarioSpec(
        name="spread_spike",
        description="Widen bid/ask spread by a factor within a window.",
        start_ratio=0.4,
        duration_ratio=0.2,
        spread_factor=3.0,
    ),
    "latency_spike": ScenarioSpec(
        name="latency_spike",
        description="Add execution latency within a window.",
        start_ratio=0.4,
        duration_ratio=0.2,
        latency_ms=250,
    ),
    "volatility_spike": ScenarioSpec(
        name="volatility_spike",
        description="Inject synthetic price noise within a window.",
        start_ratio=0.4,
        duration_ratio=0.2,
        volatility_factor=2.0,
    ),
}


def get_scenario(name: str) -> ScenarioSpec:
    if name not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {name}")
    return SCENARIOS[name]
