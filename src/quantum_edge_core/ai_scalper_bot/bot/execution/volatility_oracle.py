import math
import numpy as np
import collections
from typing import List, Dict, Any


class VolatilityOracle:
    """
    Online Volatility Oracle for Dynamic Grid DCA.
    Calculates volatility index based on a rolling window of 1-minute close prices.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: Strategy configuration dictionary.
                    Required: 'grid_min_spacing_pct', 'grid_max_spacing_pct', 'volatility_window_days'
        """
        self.config = config
        self.days = self.config.get("volatility_window_days", 7)
        self.max_len = int(self.days * 1440)  # 1440 mins per day
        self.price_history = collections.deque(maxlen=self.max_len)

        self.grid_min_spacing_pct = self.config.get("grid_min_spacing_pct", 0.002)
        self.grid_max_spacing_pct = self.config.get("grid_max_spacing_pct", 0.01)

    def add_close_price(self, price: float):
        """Adds a new 1-minute close price to the rolling history."""
        if price > 0:
            self.price_history.append(price)

    def update_from_kline(self, close_price: float, high: float = None, low: float = None):
        """Updates the oracle history from a kline."""
        self.add_close_price(close_price)

    def get_atr(self) -> float:
        """Returns the current ATR."""
        return self.calculate_atr()

    def calculate_volatility_index(self) -> float:
        """
        Calculates the volatility index based on the price history.
        Returns a normalized value between 0.0 (calm) and 1.0 (storm).
        """
        history = list(self.price_history)
        if len(history) < 1440:  # minimum 1 day of data
            return 0.5  # neutral value

        returns = [math.log(p2 / p1) for p1, p2 in zip(history[:-1], history[1:])]
        std_dev = np.std(returns) * 100  # in percent

        # Normalize: 0.0 = very calm, 1.0 = storm
        vol_index = min(max(std_dev / 2.5, 0.0), 1.0)  # calibrated for crypto
        return float(vol_index)

    def get_dynamic_grid_spacing(self, vol_index: float) -> float:
        """
        Calculates the dynamic grid spacing percentage based on the current volatility index.
        """
        return self.grid_min_spacing_pct + vol_index * (
            self.grid_max_spacing_pct - self.grid_min_spacing_pct
        )

    def calculate_atr(self) -> float:
        """
        Calculates 7-day Trimmed Range Volatility (TRV).
        Removes the top and bottom 5% outliers of absolute returns to stabilize ATR.
        Returns the absolute price move estimate.
        """
        history = list(self.price_history)
        if len(history) < 2:
            return 0.0

        # Calculate absolute price differences
        diffs = [abs(p2 - p1) for p1, p2 in zip(history[:-1], history[1:])]

        # Sort to trim outliers (Trimmed Range Volatility)
        diffs.sort()
        n = len(diffs)
        trim_idx = max(1, int(n * 0.05))

        trimmed_diffs = (
            diffs[trim_idx:-trim_idx] if len(diffs) > 2 * trim_idx else diffs
        )

        if not trimmed_diffs:
            return 0.0

        return sum(trimmed_diffs) / len(trimmed_diffs)
