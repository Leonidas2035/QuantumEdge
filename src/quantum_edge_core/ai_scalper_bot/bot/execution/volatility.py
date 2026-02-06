"""
Online Volatility Calculator.
Calculates Average True Range (ATR) incrementally using Welford's-like online updates (EMA).
"""
from typing import Optional

class OnlineVolatility:
    """
    Calculates volatility (ATR) incrementally.
    Uses Exponential Moving Average (EMA) to smooth the True Range.
    """
    def __init__(self, alpha: float = 0.01):
        """
        Args:
            alpha: Smoothing factor for EMA (0 < alpha <= 1).
                   Similar to 2/(N+1) for N-period EMA.
                   alpha=0.01 is approx 200 ticks.
        """
        self.alpha = alpha
        self.atr: float = 0.0
        self.prev_price: Optional[float] = None

    def update(self, price: float) -> float:
        """
        Updates the internal ATR state with a new price.
        
        Args:
            price: Current market price (Trade or Mid).
            
        Returns:
            Current ATR value.
        """
        if self.prev_price is None:
            self.prev_price = price
            return 0.0
        
        # True Range Calculation
        # For continuous tick series: TR = |High - Low| approx |Current - Prev|
        # Note: Standard TR uses High/Low/Close of bars. 
        # For ticks, we treat each tick as a 'period' or just measure noise.
        # Volatility here = Average Absolute Tick Change.
        tr = abs(price - self.prev_price)
        
        # EMA Calculation: Value = alpha * new + (1-alpha) * old
        if self.atr == 0.0:
            self.atr = tr # Initialize with first diff
        else:
            self.atr = (self.alpha * tr) + ((1 - self.alpha) * self.atr)
            
        self.prev_price = price
        return self.atr
