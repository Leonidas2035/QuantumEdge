"""DDN configuration models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class DDNProfile:
    name: str
    target: float
    band_low: float
    band_high: float
    force_hedge: bool = False


@dataclass
class DDNConfig:
    profiles: Dict[str, DDNProfile] = field(default_factory=dict)
    max_band_abs: float = 0.8
    max_margin_usage: float = 0.5
    min_distance_to_liq_bps: float = 250.0
    max_step_notional_usd: float = 5000.0
    min_step_notional_usd: float = 50.0
    max_steps_per_minute: int = 6
    cooldown_ms_after_reject: int = 1000
    panic_on_lag_ms: int = 5000
    taker_fee_bps: float = 7.5
    maker_fee_bps: float = 2.0
    expected_slippage_bps_market: float = 5.0
    funding_weight: float = 1.0
    min_expected_edge_bps: float = 1.0
    max_cost_bps_per_step: float = 20.0
    volatility_window: int = 30
    step_volatility_scale: float = 1.0
    max_volatility_bps_atr: float = 150.0

    @staticmethod
    def default() -> "DDNConfig":
        profiles = {
            "neutral": DDNProfile(
                name="neutral", target=0.0, band_low=-0.10, band_high=0.10
            ),
            "trend": DDNProfile(
                name="trend", target=0.0, band_low=-0.60, band_high=0.60
            ),
            "panic": DDNProfile(
                name="panic",
                target=0.0,
                band_low=-0.05,
                band_high=0.05,
                force_hedge=True,
            ),
        }
        return DDNConfig(profiles=profiles)
