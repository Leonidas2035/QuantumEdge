import numpy as np
import structlog
from typing import List

logger = structlog.get_logger(__name__)

class VolatilityOracle:
    def __init__(self, period: int = 14):
        self.period = period

    def calculate_multiplier(self, high_prices: List[float], low_prices: List[float], close_prices: List[float]) -> float:
        """
        Розраховує ATR і повертає множник для розширення сітки.
        Якщо волатильність висока, повертає > 1.0 (сітка розширюється).
        """
        if len(close_prices) < self.period + 1:
            return 1.0 # Базовий множник, якщо недостатньо даних

        # Спрощений розрахунок True Range для прикладу
        trs = []
        for i in range(1, len(close_prices)):
            hl = high_prices[i] - low_prices[i]
            hc = abs(high_prices[i] - close_prices[i-1])
            lc = abs(low_prices[i] - close_prices[i-1])
            trs.append(max(hl, hc, lc))
        
        atr = np.mean(trs[-self.period:])
        current_close = close_prices[-1]
        atr_pct = (atr / current_close) * 100

        # Нормалізація (приклад: якщо звичайний ATR = 0.5%, то при 1% множник буде 2.0)
        base_atr_pct = 0.5 
        multiplier = max(1.0, atr_pct / base_atr_pct)
        
        return round(multiplier, 2)
