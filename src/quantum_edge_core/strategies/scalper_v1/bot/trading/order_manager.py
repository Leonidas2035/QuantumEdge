"""
Order Manager.
Handles sizing, validation, and execution logic closer to the exchange.
"""

from __future__ import annotations
import logging
from typing import Dict, Any, Optional
from quantum_edge_core.supervisor.domain.models import PolicyContract, TradingMode
from quantum_edge_core.strategies.scalper_v1.bot.trading.smart_executor import SmartExecutor

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Manages order logic, sizing, and risk checks before sending to execution.
    Using SmartExecutor for actual placement.
    """

    def __init__(self, config: Dict[str, Any] = None, executor: Optional[SmartExecutor] = None):
        self.config = config or {}
        self.executor = executor
        # Hardcoded min notional for now or load from ExchangeInfo
        self.min_notional = 10.0

    def calculate_entry_size(
        self, symbol: str, price: float, equity: float, policy: PolicyContract, base_risk_pct: float = 0.01
    ) -> float:
        """
        Calculate position size based on Policy and Volatility.
        Size = (Equity * BaseRisk) * RiskMultiplier * VolatilityScalar
        """
        if price <= 0:
            return 0.0

        if policy.mode == TradingMode.FREEZE or policy.mode == TradingMode.HALT:
            return 0.0

        # 1. Base Size (e.g. 1% of equity)
        # In real HFT, this might be Kelly Criterion or more complex.
        # Here we use simple percentage of equity.
        base_size_usd = equity * base_risk_pct

        # 2. Apply Policy Multipliers
        # Risk Multiplier (from Hard Risk or Manual)
        # Volatility Scalar (from Adaptive Sizing)
        final_size_usd = base_size_usd * policy.risk_multiplier * policy.volatility_scalar

        # 3. Cap at Max Position Size (from Policy)
        final_size_usd = min(final_size_usd, policy.max_position_size)

        # 4. Check Min Notional
        if final_size_usd < self.min_notional:
            # Optionally log rejection
            # logger.debug(f"Size {final_size_usd} below min notional {self.min_notional}")
            return 0.0

        # Convert to Qty
        qty = final_size_usd / price

        return qty

    def validate_order(self, order: Dict[str, Any], policy: PolicyContract) -> bool:
        """
        Final check against policy before sending.
        """
        side = order.get("side", "").upper()
        if side == "BUY" and not policy.long_allowed:
            return False
        if side == "SELL" and not policy.short_allowed:
            return False
        if policy.close_only and not order.get("reduce_only", False):
            return False

        return True

    async def execute_order(
        self, symbol: str, side: str, qty: float, urgency: str = "MEDIUM", reduce_only: bool = False
    ) -> Dict[str, Any]:
        """
        Delegates execution to SmartExecutor.
        """
        if not self.executor:
            logger.error("No executor attached to OrderManager")
            return {}

        return await self.executor.execute_order(
            symbol=symbol, side=side, qty=qty, urgency=urgency, reduce_only=reduce_only
        )
