"""Simple 4-rule Risk Guard for Lean Execution Core."""

from __future__ import annotations

from dataclasses import dataclass

from quantum_edge_core.contracts.lean import TradeDecision


@dataclass(slots=True)
class RiskConfig:
    allowed_symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    max_margin_vst: float = 1000.0
    max_leverage: int = 50
    max_open_positions: int = 3
    require_sl_on_open: bool = True
    entry_enabled: bool = True


class LeanRiskGuard:
    """Enforces 4 essential pre-trade safety checks on entries without blocking exits."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    def validate(
        self,
        decision: TradeDecision,
        open_positions_count: int,
        current_price: float,
    ) -> tuple[bool, str]:
        # Exits and HOLD are never blocked by entry limits
        if decision.action in ("HOLD", "CLOSE_LONG", "CLOSE_SHORT"):
            return True, "OK"

        # 1. Entry enabled & allowed symbol
        if not self.config.entry_enabled:
            return False, "ENTRY_DISABLED: new position entries are disabled"
        if decision.symbol not in self.config.allowed_symbols:
            return False, f"SYMBOL_NOT_ALLOWED: {decision.symbol}"

        # 2. Margin and leverage bounds
        if decision.margin_vst <= 0 or decision.margin_vst > self.config.max_margin_vst:
            return (
                False,
                f"INVALID_MARGIN: margin_vst={decision.margin_vst} must be in (0, {self.config.max_margin_vst}]",
            )
        if decision.leverage is None or decision.leverage < 1 or decision.leverage > self.config.max_leverage:
            return (
                False,
                f"INVALID_LEVERAGE: leverage={decision.leverage} must be in [1, {self.config.max_leverage}]",
            )

        # 3. Open positions cap
        if open_positions_count >= self.config.max_open_positions:
            return (
                False,
                f"MAX_OPEN_POSITIONS_REACHED: {open_positions_count} >= {self.config.max_open_positions}",
            )

        # 4. Stop-loss presence and directional validity
        if self.config.require_sl_on_open:
            ref_price = (
                decision.entry_price
                if (decision.entry_price is not None and decision.entry_price > 0)
                else current_price
            )
            if decision.sl_price is None or decision.sl_price <= 0:
                return False, "MISSING_STOP_LOSS: sl_price is required for opening positions"
            if ref_price <= 0:
                return False, f"INVALID_REFERENCE_PRICE: ref_price={ref_price}"
            if decision.action == "OPEN_LONG" and decision.sl_price >= ref_price:
                return (
                    False,
                    f"INVALID_STOP_LOSS: OPEN_LONG requires sl_price ({decision.sl_price}) < ref_price ({ref_price})",
                )
            if decision.action == "OPEN_SHORT" and decision.sl_price <= ref_price:
                return (
                    False,
                    f"INVALID_STOP_LOSS: OPEN_SHORT requires sl_price ({decision.sl_price}) > ref_price ({ref_price})",
                )

        return True, "OK"
