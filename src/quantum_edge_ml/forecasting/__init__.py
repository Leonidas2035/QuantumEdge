"""QuantumEdge causal quantitative forecasting package (Phase 6)."""

from quantum_edge_ml.forecasting.pipeline import (
    AdaptiveConformalCalibrator,
    CausalHMMFilter,
    EGARCHVolatilityModel,
    ForecastBundleV1,
    ForecastPipeline,
    MultiHeadLGBMForecaster,
    ShadowTSFMChallenger,
    compute_calibration_metrics,
    compute_triple_barrier_labels,
    purged_walk_forward_splits,
)

__all__ = [
    "AdaptiveConformalCalibrator",
    "CausalHMMFilter",
    "EGARCHVolatilityModel",
    "ForecastBundleV1",
    "ForecastPipeline",
    "MultiHeadLGBMForecaster",
    "ShadowTSFMChallenger",
    "compute_calibration_metrics",
    "compute_triple_barrier_labels",
    "purged_walk_forward_splits",
]
