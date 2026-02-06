import ujson
from collections import deque
from typing import Optional
from .models import MarketState, MarketTick

class OrderBookCache:
    """
    O(1) Кеш мікроструктури.
    Зберігає актуальний Best Bid/Ask без Pandas.
    """
    def __init__(self, history_len: int = 1000):
        self._history = deque(maxlen=history_len)
        self.snapshot: Optional[MarketState] = None

    def update(self, tick_data: dict) -> None:
        try:
            # Швидкий парсинг
            p = float(tick_data['p'])
            q = float(tick_data['q'])
            t = int(tick_data['t'])
            is_buyer = tick_data.get('m', False)
            
            # Емуляція стакану (для спрощення беремо тік як best price)
            # В реальному L2 тут було б оновлення по id ордера
            self.snapshot = MarketState(
                timestamp=t,
                best_bid=p if is_buyer else p - 0.01, # Спрощена логіка для тесту
                best_bid_qty=q,
                best_ask=p + 0.01 if is_buyer else p,
                best_ask_qty=q,
                last_price=p
            )
            
        except KeyError:
            pass # Ігноруємо биті пакети
