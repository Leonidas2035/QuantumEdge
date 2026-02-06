from collections import deque
from ..core.models import MarketTick

class VpinCalculator:
    """Volume-Synchronized Probability of Informed Trading"""
    def __init__(self, bucket_vol=1.0, window=50):
        self.bucket_target = bucket_vol
        self.window = window
        self.buckets = deque(maxlen=window)
        self.current_vol = 0.0
        self.buy_vol = 0.0
        self.sell_vol = 0.0
        self.last_vpin = 0.0

    def update(self, tick: dict) -> float:
        try:
            qty = float(tick['q'])
            is_buyer_maker = tick.get('m', False) # True = Sell, False = Buy
            
            self.current_vol += qty
            if not is_buyer_maker: # Buy
                self.buy_vol += qty
            else: # Sell
                self.sell_vol += qty
                
            # Якщо бакет заповнений
            if self.current_vol >= self.bucket_target:
                self.buckets.append((self.buy_vol, self.sell_vol))
                self._recalc()
                self.current_vol = 0.0
                self.buy_vol = 0.0
                self.sell_vol = 0.0
                
            return self.last_vpin
        except:
            return 0.0

    def _recalc(self):
        if len(self.buckets) < 1: return
        total_vol = 0
        imbalance = 0
        for b_buy, b_sell in self.buckets:
            total_vol += (b_buy + b_sell)
            imbalance += abs(b_buy - b_sell)
        
        if total_vol > 0:
            self.last_vpin = imbalance / total_vol
