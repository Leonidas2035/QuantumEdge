
import os

class Config:
    def __init__(self):
        # --- BINANCE CREDENTIALS ---
        self.binance_api_key = os.getenv("BINANCE_API_KEY", "")
        self.binance_secret = os.getenv("BINANCE_API_SECRET", "")
        self.use_testnet = os.getenv("BINANCE_TESTNET", "1") in {"1", "true", "True"}
        
        # --- SYSTEM PORTS ---
        self.market_data_port = int(os.getenv("MARKET_DATA_ZMQ_PORT", "5555"))
        self.supervisor_port = 5557
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
