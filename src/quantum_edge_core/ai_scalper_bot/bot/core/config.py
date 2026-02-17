
import os
import yaml
from pathlib import Path

class Config:
    def __init__(self):
        # --- BINANCE CREDENTIALS ---
        self.binance_api_key = os.getenv("BINANCE_API_KEY", "")
        self.binance_secret = os.getenv("BINANCE_API_SECRET", "")
        self.use_testnet = os.getenv("BINANCE_TESTNET", "1") in {"1", "true", "True"}
        
        # --- SYSTEM PORTS ---
        self.market_data_port = int(os.getenv("MARKET_DATA_ZMQ_PORT", "5555"))

        # Load from Env or Services YAML
        self.service_id = os.getenv("QE_BOT_ID", "ai_scalper_bot")
        self.telemetry_port = int(os.getenv("QE_BOT_TELEMETRY_PORT", "5557"))
        self.policy_port = int(os.getenv("QE_BOT_POLICY_PORT", "5558"))

        if "QE_BOT_ID" not in os.environ:
             self._load_from_services_yaml()

        self.supervisor_port = self.telemetry_port # Legacy support

        self.symbol = os.getenv("MARKET_DATA_SYMBOLS", "BTCUSDT").split(",")[0]
        
        # --- STRATEGY CONFIG ---
        # Moving strategy config here for centralization during UAT
        self.strategy_config = {
            "symbol": self.symbol,
            "base_order_size_q": 0.001, # VST BTC size
            "dry_run": False, # Actual VST Execution on BingX
            "ofi_entry_threshold": 0.1,  # Low threshold for testing
            "take_profit_pct": 0.001,
            "atr_alpha": 0.1,
            # Core Strategy Params
            "safety_order_multiplier": 1.5,
            "hedge_trigger_dd": 0.02,
            "grid_step_atr_mult": 2.0
        }

    def _load_from_services_yaml(self):
        try:
            path = Path("config/services.yaml")
            if path.exists():
                with open(path, "r") as f:
                    data = yaml.safe_load(f)
                    bot_cfg = data.get("services", {}).get("bot", {})
                    self.service_id = bot_cfg.get("id", self.service_id)
                    zmq = bot_cfg.get("zmq", {})
                    self.telemetry_port = int(zmq.get("telemetry_port", self.telemetry_port))
                    self.policy_port = int(zmq.get("policy_port", self.policy_port))
        except Exception as e:
            print(f"Warning: Failed to load config/services.yaml: {e}")
