import os

class Config:
    def __init__(self):
        # BINGX DEMO KEYS
        self.bingx_api_key = "rB6jgu6mI3c89GYc2IanT2C06BDvcWSxqwYXXrtFxS60SGMUTsGsH4gf1JaZIMlqMKk9trlZJh3ir83omKieQ"
        self.bingx_secret = "J1iuiF8j5AaXbBVTNaVXloaPUbpA7MyssfdLKgjvzrc8fvO91f2XcQk6ASY7uviXUxNtUSW5jKIv9bpWRvXFQ"
        self.use_sandbox = True
        
        # PORTS
        self.market_data_port = 5555
        self.supervisor_port = 5557
        self.symbol = "BTC-USDT"
