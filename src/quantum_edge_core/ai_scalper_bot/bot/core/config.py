import os
import yaml
from pathlib import Path


class Config:
    def __init__(self):
        # --- BINANCE CREDENTIALS ---
        self.binance_api_key = os.getenv("BINANCE_API_KEY", "")
        self.binance_secret = os.getenv("BINANCE_API_SECRET", "")

        # Default to True, but will be checked against YAML and Env
        self.use_testnet = True

        # --- SYSTEM PORTS ---
        self.market_data_port = int(os.getenv("MARKET_DATA_ZMQ_PORT", "5555"))

        # Initialize defaults
        self.service_id = "ai_scalper_bot"
        self.telemetry_port = 5557
        self.policy_port = 5558

        # Always try to load from services.yaml to get testnet setting and defaults
        self._load_from_services_yaml()

        # Override with Environment Variables (Precedence: Env > YAML)
        if "QE_BOT_ID" in os.environ:
            self.service_id = os.environ["QE_BOT_ID"]

        if "QE_BOT_TELEMETRY_PORT" in os.environ:
            self.telemetry_port = int(os.environ["QE_BOT_TELEMETRY_PORT"])

        if "QE_BOT_POLICY_PORT" in os.environ:
            self.policy_port = int(os.environ["QE_BOT_POLICY_PORT"])

        # Specific Logic for Testnet: Env Var > YAML > Default
        env_testnet = os.getenv("BINANCE_TESTNET")
        if env_testnet is not None:
            self.use_testnet = env_testnet.lower() in {"1", "true", "yes", "on"}

        self.supervisor_port = self.telemetry_port  # Legacy support

        self.symbol = os.getenv("MARKET_DATA_SYMBOLS", "BTCUSDT").split(",")[0]

        # --- STRATEGY CONFIG ---
        # Moving strategy config here for centralization during UAT
        self.strategy_config = {
            "symbol": self.symbol,
            "base_order_size_q": 0.001,  # VST BTC size
            "dry_run": False,  # Actual VST Execution on BingX
            "ofi_entry_threshold": 0.1,  # Low threshold for testing
            "take_profit_pct": 0.001,
            "atr_alpha": 0.1,
            # Core Strategy Params
            "safety_order_multiplier": 1.5,
            "hedge_trigger_dd": 0.02,
            "grid_step_atr_mult": 2.0,
        }

    def _load_from_services_yaml(self):
        try:
            path = Path("config/services.yaml")
            if path.exists():
                with open(path, "r") as f:
                    data = yaml.safe_load(f)
                    bot_cfg = data.get("services", {}).get("bot", {})
                    self.service_id = bot_cfg.get("id", self.service_id)

                    # Load testnet setting if present
                    if "testnet" in bot_cfg:
                        self.use_testnet = bot_cfg["testnet"]

                    zmq = bot_cfg.get("zmq", {})
                    self.telemetry_port = int(
                        zmq.get("telemetry_port", self.telemetry_port)
                    )
                    self.policy_port = int(zmq.get("policy_port", self.policy_port))
        except Exception as e:
            print(f"Warning: Failed to load config/services.yaml: {e}")
