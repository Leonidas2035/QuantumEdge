import os
import yaml
from pathlib import Path


class Config:
    def __init__(self):
        # --- BINANCE CREDENTIALS (Legacy) ---
        self.binance_api_key = os.getenv("BINANCE_API_KEY", "")
        self.binance_secret = os.getenv("BINANCE_API_SECRET", "")

        self.binance_testnet_api_key = os.getenv("BINANCE_TESTNET_API_KEY", "")
        self.binance_testnet_secret = os.getenv("BINANCE_TESTNET_SECRET_KEY", "")

        # --- BINGX CREDENTIALS ---
        self.bingx_api_key = os.getenv("BINGX_API_KEY", "")
        self.bingx_secret = os.getenv("BINGX_SECRET", "")

        self.bingx_testnet_api_key = os.getenv("BINGX_TESTNET_API_KEY", "")
        self.bingx_testnet_secret = os.getenv("BINGX_TESTNET_SECRET", "")

        # Determine testnet routing
        use_testnet_env = os.getenv("USE_TESTNET", os.getenv("BINANCE_TESTNET", "1"))
        self.use_testnet = str(use_testnet_env).lower() in {"1", "true", "t"}

        # Execution mode: paper | bingx | binance
        self.execution_mode = os.getenv("EXECUTION_MODE", "paper").lower()
        self.bingx_position_mode = os.getenv("BINGX_POSITION_MODE", "hedge").lower()

        # --- SYSTEM PORTS ---
        self.market_data_port = int(os.getenv("MARKET_DATA_ZMQ_PORT", "5555"))

        # Load from Env or Services YAML
        env_id = os.getenv("QE_BOT_ID", "ai_scalper_bot")
        self.service_id = "ai_scalper_bot" if env_id == "ai_scalper" else env_id
        self.telemetry_port = 5557
        env_bot_id = os.getenv("QE_BOT_ID")
        if env_bot_id in (None, "ai_scalper", "ai_scalper_bot"):
            env_port = os.getenv("QE_BOT_TELEMETRY_PORT")
            if env_port:
                try:
                    self.telemetry_port = int(env_port)
                except ValueError:
                    pass
        self.policy_port = int(os.getenv("QE_BOT_POLICY_PORT", "5559"))
        self.trading_mode = "scalp"

        if "QE_BOT_ID" not in os.environ:
            self._load_from_services_yaml()

        # Force SCALP mode strictly
        self.trading_mode = "scalp"

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
            # Grid Config
            "grid_min_spacing_pct": 0.002,  # 0.2%
            "grid_max_spacing_pct": 0.01,  # 1.0%
            "volatility_window_days": 7,
            "grid_levels_below": 15,  # кількість ордерів нижче поточної ціни
            "grid_levels_above": 15,  # кількість ордерів вище поточної ціни
        }

    def _load_from_services_yaml(self):
        try:
            path = Path("config/services.yaml")
            if path.exists():
                with open(path, "r") as f:
                    data = yaml.safe_load(f)
                    bot_cfg = data.get("services", {}).get("bot", {})
                    self.service_id = bot_cfg.get("id", self.service_id)
                    self.trading_mode = bot_cfg.get("trading_mode", self.trading_mode)
                    zmq = bot_cfg.get("zmq", {})
                    self.telemetry_port = int(
                        zmq.get("telemetry_port", self.telemetry_port)
                    )
                    self.policy_port = int(zmq.get("policy_port", self.policy_port))
        except Exception as e:
            print(f"Warning: Failed to load config/services.yaml: {e}")
