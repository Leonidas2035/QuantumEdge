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
    total_capital_vst: float = 150000.0
    max_orders_per_side: int = 15
    initial_order_qty: float = 0.01
    order_value_pct_balance: float = 0.03
    min_order_value_vst: float = 500.0
    
    @property
    def total_tp_pct(self) -> float:
        return self.base_profit_pct + self.funding_comp_pct
    
    @classmethod
    def load(cls) -> "DynDCAConfig":
        import yaml
        
        cfg = cls()
        
        # Paths to try
        paths_to_try = ["config/dca.yaml", "../../config/dca.yaml"]
        yaml_data = {}
        for path in paths_to_try:
            if os.path.exists(path):
                try:
                    with open(path, "r") as f:
                        yaml_data = yaml.safe_load(f) or {}
                    break
                except Exception as e:
                    logger.warning("Failed to parse dca.yaml", path=path, error=str(e))
        
        if yaml_data:
            cfg.bot_id = yaml_data.get("bot_id", cfg.bot_id)
            cfg.mode = yaml_data.get("mode", cfg.mode)
            
            md = yaml_data.get("market_data", {})
            if isinstance(md, dict):
                cfg.zmq_market_data_port = md.get("zmq_port", cfg.zmq_market_data_port)
                
            telemetry = yaml_data.get("telemetry", {})
            if isinstance(telemetry, dict):
                cfg.zmq_telemetry_port = telemetry.get("zmq_port", cfg.zmq_telemetry_port)
                
            strategy = yaml_data.get("strategy", {})
            if isinstance(strategy, dict):
                cfg.grid_spacing_pct = strategy.get("grid_spacing_pct", cfg.grid_spacing_pct)
                cfg.gamma = strategy.get("gamma", cfg.gamma)
                
            execution = yaml_data.get("execution", {})
            if isinstance(execution, dict):
                cfg.base_profit_pct = execution.get("base_profit_pct", cfg.base_profit_pct)
                cfg.funding_comp_pct = execution.get("funding_comp_pct", cfg.funding_comp_pct)
                cfg.total_capital_vst = execution.get("total_capital_vst", 150000.0)
                cfg.max_orders_per_side = execution.get("max_orders_per_side", 15)
                cfg.initial_order_qty = execution.get("initial_order_qty", 0.01)
                cfg.order_value_pct_balance = execution.get("order_value_pct_balance", 0.03)
                cfg.min_order_value_vst = execution.get("min_order_value_vst", 500.0)

        # Overwrite telemetry port with QE_BOT_TELEMETRY_PORT only if isolated for DynDCA
        env_bot_id = os.environ.get("QE_BOT_ID")
        if env_bot_id in ("dyndca", "dyndca_v1"):
            env_port = os.environ.get("QE_BOT_TELEMETRY_PORT")
            if env_port:
                try:
                    cfg.zmq_telemetry_port = int(env_port)
                except ValueError:
                    logger.warning("Invalid QE_BOT_TELEMETRY_PORT environment variable", value=env_port)

        # Overwrite market data ZMQ port with MARKET_DATA_ZMQ_PORT if defined
        env_md_port = os.environ.get("MARKET_DATA_ZMQ_PORT")
        if env_md_port:
            try:
                cfg.zmq_market_data_port = int(env_md_port)
            except ValueError:
                logger.warning("Invalid MARKET_DATA_ZMQ_PORT environment variable", value=env_md_port)

        logger.info("Loaded DynDCA configuration", mode=cfg.mode, zmq_telemetry_port=cfg.zmq_telemetry_port)
        return cfg
