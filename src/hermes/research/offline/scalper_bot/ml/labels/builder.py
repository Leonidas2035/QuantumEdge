"""Label builder for multi-horizon classification targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LabelConfig:
    horizons: tuple[int, ...] = (1, 5, 30)
    label_mode: str = "seconds"
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    label_thr_bps: float = 2.0
    ignore_thr_bps: float = 0.0

    def effective_threshold(self) -> float:
        effective_bps = max(self.label_thr_bps, self.fee_bps + self.slippage_bps)
        return effective_bps / 10_000.0

    def ignore_threshold(self) -> float:
        return self.ignore_thr_bps / 10_000.0


def build_labels(
    bars: pd.DataFrame,
    config: LabelConfig,
    price_col: str = "price",
) -> pd.DataFrame:
    if price_col not in bars.columns:
        raise KeyError(f"Missing price column: {price_col}")

    price = bars[price_col].astype(float)
    eff_thr = config.effective_threshold()
    ignore_thr = config.ignore_threshold()
    labels: Dict[str, pd.Series] = {}

    for horizon in config.horizons:
        if config.label_mode not in ("seconds", "ticks"):
            raise ValueError(f"Unsupported label_mode: {config.label_mode}")
        shift_steps = int(horizon)
        future_price = price.shift(-shift_steps)
        fut_ret = (future_price / price) - 1.0
        labels[f"fut_ret_h{horizon}"] = fut_ret
        y_up = (fut_ret > eff_thr).astype(float)
        if ignore_thr > 0:
            y_up = y_up.mask(fut_ret.abs() < ignore_thr, np.nan)
        labels[f"y_up_h{horizon}"] = y_up

    return pd.DataFrame(labels, index=bars.index)


def parse_horizons(raw: Iterable[int | str]) -> tuple[int, ...]:
    horizons = []
    for item in raw:
        horizons.append(int(item))
    return tuple(sorted(set(horizons)))
