import os
from dataclasses import dataclass, field
import structlog

logger = structlog.get_logger(__name__)

@dataclass
class DynDCAConfig:
    bot_id: str = "dyndca_v1"
    mode: str = "paper"  # live, paper, demo
    zmq_market_data_port: int = 5555
    zmq_telemetry_port: int = 5567  # Окремий порт для Supervisor
    grid_spacing_pct: float = 0.5
    gamma: float = 1.2
    base_profit_pct: float = 1.0      # Базовий цільовий прибуток
    funding_comp_pct: float = 0.2     # Компенсація за фандінг та комісії
    
    @property
    def total_tp_pct(self) -> float:
        return self.base_profit_pct + self.funding_comp_pct
    
    @classmethod
    def load(cls) -> "DynDCAConfig":
        # У майбутньому тут буде парсинг з config/dca.yaml
        logger.info("Loaded DynDCA configuration", mode="paper")
        return cls()
