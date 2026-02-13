class Config:
    def __init__(self):
        # --- BINGX CREDENTIALS (TEMPORARY) ---
        self.bingx_api_key = "rB6jgu6mI3c89GYc2IanT2C06BDvcWSxqwYXXrtFxS60SGMUTsGsH4gf1JaZIMlqMKk9trlZJh3ir83omKieQ"
        self.bingx_secret = "J1iuiF8j5AaXbBVTNaVXloaPUbpA7MyssfdLKgjvzrc8fvO91f2XcQk6ASY7uviXUxNtUSW5jKIv9bpWRvXFQ"
        self.use_sandbox = True  # BingX VST

        # --- SYSTEM PORTS ---
        self.market_data_port = 5555
        self.supervisor_port = 5557
        self.symbol = "BTC-USDT"  # BingX Swap format

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
