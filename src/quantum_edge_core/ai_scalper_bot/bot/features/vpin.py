"""
Volume-Synchronized Probability of Informed Trading (VPIN).
Updates on volume buckets to measure flow toxicity.
"""
from collections import deque
from typing import Optional, Deque
from quantum_edge_core.ai_scalper_bot.bot.core.models import MarketTick

class VpinCalculator:
    """
    Volume-Synchronized Probability of Informed Trading.
    Updates on volume buckets, not time.
    """
    def __init__(self, bucket_vol: float, window: int):
        self.bucket_target = bucket_vol
        self.window = window
        self.buckets: Deque[tuple] = deque(maxlen=window)
        
        # Accumulators
        self.cur_vol = 0.0
        self.cur_buy = 0.0
        self.cur_sell = 0.0
        self.last_vpin = 0.0

    def update(self, tick: MarketTick) -> Optional[float]:
        """
        Adds tick volume to current bucket. 
        If bucket fills, calculates VPIN over the window.
        
        Returns:
            New VPIN value if bucket filled, else None (or potentially last val).
            Prompt says "Return new VPIN value. If bucket not full, return None (or previous value)."
            I will return None to signal no update, letting Facade decide.
        """
        vol = tick.quantity
        
        # Classify Buy/Sell
        # is_buyer_maker=True -> Selle agressor (Price went down usually) -> Volume is SELL
        # is_buyer_maker=False -> Buyer agressor (Price went up usually) -> Volume is BUY
        # (Binance standard)
        if tick.is_buyer_maker:
            self.cur_sell += vol
        else:
            self.cur_buy += vol
            
        self.cur_vol += vol
        
        if self.cur_vol >= self.bucket_target:
            # Bucket Full
            # Push (buy, sell) tuple
            self.buckets.append((self.cur_buy, self.cur_sell))
            
            # Reset Accumulators
            # In precise VPIN, we might spill over excess volume to next bucket.
            # Simplified approach: Reset all.
            self.cur_vol = 0.0
            self.cur_buy = 0.0
            self.cur_sell = 0.0
            
            # Calculate VPIN if we have enough data (or partial)
            # Formula: sum(|V_buy - V_sell|) / (n * V_bucket)
            # n = number of buckets in window
            
            if len(self.buckets) > 0:
                imbalance_sum = sum(abs(b[0] - b[1]) for b in self.buckets)
                total_volume = len(self.buckets) * self.bucket_target
                
                # Avoid div by zero
                if total_volume > 0:
                    self.last_vpin = imbalance_sum / total_volume
                else:
                    self.last_vpin = 0.0
    
            return self.last_vpin
            
        return None
