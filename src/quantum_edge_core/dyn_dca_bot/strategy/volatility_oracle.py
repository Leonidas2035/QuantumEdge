import numpy as np
import structlog
from typing import List, Dict, Any
from dataclasses import dataclass
from enum import Enum

logger = structlog.get_logger(__name__)

class MarketRegime(Enum):
    CALM = "CALM"                 # Вузька сітка, агресивний DCA
    CHOPPY = "CHOPPY"             # Стандартна сітка
    HIGH_VOLATILITY = "HIGH_VOL"  # Широка сітка (множник > 1.5)
    FLASH_CRASH = "FLASH_CRASH"   # Екстремальне розширення (множник > 3.0) або зупинка

@dataclass
class OracleState:
    multiplier: float
    regime: MarketRegime
    realized_volatility: float
    z_score: float
    spread_anomaly: bool

class EnterpriseVolatilityOracle:
    def __init__(self, window_size: int = 60, ewma_alpha: float = 0.05):
        """
        :param window_size: Вікно для розрахунку історичної норми (Z-score)
        :param ewma_alpha: Коефіцієнт чутливості EWMA (чим ближче до 1, тим швидша реакція)
        """
        self.window_size = window_size
        self.ewma_alpha = ewma_alpha
        
        # Внутрішній стан
        self.tick_returns: List[float] = []
        self.ewma_variance: float = 0.0
        self.historical_vol_buffer: List[float] = []

    def update_tick(self, current_price: float, last_price: float):
        """
        Оновлюється при кожному трейді/тіку від MarketDataHub для миттєвої реакції.
        """
        if last_price <= 0:
            return
            
        # Логарифмічна прибутковість тіку
        tick_return = np.log(current_price / last_price)
        
        # Оновлення EWMA дисперсії (Variance)
        if self.ewma_variance == 0.0:
            self.ewma_variance = tick_return ** 2
        else:
            self.ewma_variance = (self.ewma_alpha * (tick_return ** 2)) + ((1 - self.ewma_alpha) * self.ewma_variance)

    def evaluate_regime(self, l1_spread_bps: float, atr_pct: float) -> OracleState:
        """
        Комплексна оцінка волатильності, яка викликається перед виставленням сітки.
        """
        current_vol = np.sqrt(self.ewma_variance) if self.ewma_variance > 0 else (atr_pct / 100.0)
        
        # Накопичення історії для Z-Score
        self.historical_vol_buffer.append(current_vol)
        if len(self.historical_vol_buffer) > self.window_size:
            self.historical_vol_buffer.pop(0)

        # Розрахунок Z-Score
        if len(self.historical_vol_buffer) > 10:
            mean_vol = float(np.mean(self.historical_vol_buffer))
            std_vol = float(np.std(self.historical_vol_buffer))
            # Захист від ділення на 0
            z_score = (current_vol - mean_vol) / std_vol if std_vol > 1e-8 else 0.0
        else:
            z_score = 0.0

        # Визначення аномалії спреду (якщо спред розширився більше ніж на 15 bps - маркетмейкери пішли)
        spread_anomaly = l1_spread_bps > 15.0 

        # Дерево прийняття рішень (State Machine)
        multiplier = 1.0
        regime = MarketRegime.CALM

        if z_score > 3.0 or spread_anomaly:
            regime = MarketRegime.FLASH_CRASH
            multiplier = 3.0 # Розтягуємо сітку максимально
        elif z_score > 1.5:
            regime = MarketRegime.HIGH_VOLATILITY
            multiplier = 1.5 + (z_score * 0.2) # Динамічне розширення
        elif z_score > 0.5:
            regime = MarketRegime.CHOPPY
            multiplier = 1.2
            
        logger.debug("Volatility Oracle Evaluated", 
                     regime=regime.name, 
                     multiplier=round(multiplier, 2), 
                     z_score=round(z_score, 2),
                     spread_bps=round(l1_spread_bps, 1))

        return OracleState(
            multiplier=round(multiplier, 2),
            regime=regime,
            realized_volatility=current_vol,
            z_score=round(z_score, 2),
            spread_anomaly=spread_anomaly
        )
