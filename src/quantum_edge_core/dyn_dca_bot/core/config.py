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
    
    @classmethod
    def load(cls) -> "DynDCAConfig":
        # У майбутньому тут буде парсинг з config/dca.yaml
        logger.info("Loaded DynDCA configuration", mode="paper")
        return cls()
