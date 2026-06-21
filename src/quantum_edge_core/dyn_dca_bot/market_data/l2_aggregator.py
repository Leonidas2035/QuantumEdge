import structlog
from typing import List, Dict, Any, Tuple

logger = structlog.get_logger(__name__)

class L2Aggregator:
    def __init__(self, max_depth_pct: float = 5.0, wall_multiplier: float = 10.0):
        """
        :param max_depth_pct: Максимальна відстань від мід-ціни у відсотках (скануємо тільки робочий діапазон).
        :param wall_multiplier: У скільки разів об'єм рівня має перевищувати середній об'єм, щоб вважатися "стіною".
        """
        self.max_depth_pct = max_depth_pct
        self.wall_multiplier = wall_multiplier

    def analyze_orderbook(self, l2_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Аналізує L2 снепшот та знаходить плити (стіни) ліквідності.
        Очікуваний формат l2_data: {'bids': [[price, vol], ...], 'asks': [[price, vol], ...]}
        """
        bids = self._parse_levels(l2_data.get('bids', []))
        asks = self._parse_levels(l2_data.get('asks', []))

        if not bids or not asks:
            logger.warning("Empty L2 data provided to aggregator.")
            return {"bid_walls": [], "ask_walls": []}

        # Розрахунок Mid-Price
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        mid_price = (best_bid + best_ask) / 2.0

        bid_walls = self._find_walls(bids, mid_price, side="bid")
        ask_walls = self._find_walls(asks, mid_price, side="ask")

        # Логування знайдених аномалій (debug)
        if bid_walls or ask_walls:
            logger.debug("Whale walls detected", 
                         bid_walls_count=len(bid_walls), 
                         ask_walls_count=len(ask_walls))

        return {
            "bid_walls": bid_walls,
            "ask_walls": ask_walls
        }

    def _parse_levels(self, levels: List[List[Any]]) -> List[Tuple[float, float]]:
        # Конвертація у float на випадок, якщо дані прийшли як рядки з JSON
        return [(float(p), float(v)) for p, v in levels]

    def _find_walls(self, levels: List[Tuple[float, float]], mid_price: float, side: str) -> List[Dict[str, Any]]:
        walls = []
        valid_levels = []

        # 1. Фільтрація по глибині (max_depth_pct)
        for price, vol in levels:
            distance_pct = abs(price - mid_price) / mid_price * 100
            if distance_pct <= self.max_depth_pct:
                valid_levels.append((price, vol, distance_pct))

        if not valid_levels:
            return walls

        # 2. Розрахунок середнього об'єму в робочій зоні
        total_volume = sum(vol for _, vol, _ in valid_levels)
        mean_volume = total_volume / len(valid_levels)
        threshold_volume = mean_volume * self.wall_multiplier

        # 3. Ідентифікація стін
        for price, vol, distance_pct in valid_levels:
            if vol >= threshold_volume:
                walls.append({
                    "price": price,
                    "volume": vol,
                    "distance_pct": round(distance_pct, 4),
                    "side": side,
                    "mean_vol_multiplier": round(vol / mean_volume, 2)
                })

        # Сортування: найближчі до mid_price стіни йдуть першими
        walls.sort(key=lambda x: x["distance_pct"])
        return walls
