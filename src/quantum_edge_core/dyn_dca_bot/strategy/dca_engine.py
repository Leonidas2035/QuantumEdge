import structlog
from typing import List, Dict, Any, Optional
from quantum_edge_core.dyn_dca_bot.strategy.volatility_oracle import OracleState, MarketRegime

logger = structlog.get_logger(__name__)

class DCAEngine:
    def __init__(self, grid_spacing_pct: float, gamma: float, magnet_tolerance_pct: float = 0.1):
        self.grid_spacing_pct = grid_spacing_pct
        self.gamma = gamma
        self.magnet_tolerance_pct = magnet_tolerance_pct
        self.tick_size = 0.1 # Згодом братиметься з конфігу біржі

    def calculate_next_order(
        self, 
        current_price: float, 
        average_entry: float, 
        step_index: int, 
        oracle_state: OracleState,
        walls: Dict[str, List[Dict[str, Any]]],
        side: str = "buy"
    ) -> Optional[float]:
        """
        Розраховує ідеальну ціну для наступного усереднення з урахуванням L2 плит.
        """
        # Якщо на ринку FLASH_CRASH, блокуємо ордер до заспокоєння ринку
        if oracle_state.regime == MarketRegime.FLASH_CRASH:
            logger.warning("Flash Crash detected! Delaying grid placement.")
            return None
            
        # 1. Базова математика (ATR + Gamma)
        base_step = self.grid_spacing_pct * oracle_state.multiplier
        expanded_step_pct = base_step * (self.gamma ** step_index)
        
        if side == "buy":
            math_price = average_entry * (1 - (expanded_step_pct / 100.0))
        else:
            math_price = average_entry * (1 + (expanded_step_pct / 100.0))

        # 2. Magnet Effect (Коригування за L2)
        adjusted_price = math_price
        target_walls = walls.get("bid_walls", []) if side == "buy" else walls.get("ask_walls", [])

        for wall in target_walls:
            wall_price = wall["price"]
            distance_to_wall_pct = abs(math_price - wall_price) / math_price * 100

            if distance_to_wall_pct <= self.magnet_tolerance_pct:
                if side == "buy" and math_price >= wall_price:
                    # Фронтран bid-плити (ставимо трохи вище неї)
                    adjusted_price = wall_price + self.tick_size
                    logger.info("Magnet effect applied: front-running bid wall", 
                                math_price=math_price, wall_price=wall_price, adjusted_price=adjusted_price)
                elif side == "sell" and math_price <= wall_price:
                    # Фронтран ask-плити (ставимо трохи нижче неї)
                    adjusted_price = wall_price - self.tick_size
                    logger.info("Magnet effect applied: front-running ask wall", 
                                math_price=math_price, wall_price=wall_price, adjusted_price=adjusted_price)
                break # Застосовуємо лише до найближчої знайденої стіни в межах толерантності

        return round(adjusted_price, 2)
