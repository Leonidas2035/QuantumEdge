"""Simple 4-rule Risk Guard for Lean Execution Core."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from quantum_edge_core.contracts.lean import TradeDecision

QUANTUMEDGE_DEFAULT_UNIVERSE: tuple[str, ...] = ("BNBUSDT", "LINKUSDT", "SUIUSDT")


@dataclass(slots=True)
class RiskConfig:
    allowed_symbols: tuple[str, ...] = (
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "BNBUSDT",
        "LINKUSDT",
        "SUIUSDT",
    )
    max_margin_vst: float = 1000.0
    max_leverage: int = 50
    max_open_positions: int = 3
    max_notional_per_pair_vst: float = 50_000.0
    require_sl_on_open: bool = True
    require_isolated_margin: bool = True
    enforce_sl_before_liquidation: bool = True
    liquidation_safety_factor: float = 0.80
    entry_enabled: bool = True

    @classmethod
    def for_qe_isolated_50x(
        cls,
        *,
        symbols: Sequence[str] = QUANTUMEDGE_DEFAULT_UNIVERSE,
        max_margin_vst: float = 1000.0,
        max_leverage: int = 50,
        max_open_positions: int = 3,
        max_notional_per_pair_vst: float = 50_000.0,
    ) -> RiskConfig:
        """Build isolated-margin 50x RiskConfig scoped strictly to the session universe."""
        cleaned = tuple(str(s).strip().upper() for s in symbols if str(s).strip())
        return cls(
            allowed_symbols=cleaned or QUANTUMEDGE_DEFAULT_UNIVERSE,
            max_margin_vst=float(max_margin_vst),
            max_leverage=int(max_leverage),
            max_open_positions=int(max_open_positions),
            max_notional_per_pair_vst=float(max_notional_per_pair_vst),
            require_sl_on_open=True,
            require_isolated_margin=True,
            enforce_sl_before_liquidation=True,
            liquidation_safety_factor=0.80,
            entry_enabled=True,
        )


class LeanRiskGuard:
    """Enforces pre-trade safety checks on entries without blocking exits."""

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

        # 2. Margin, leverage, and max notional per pair bounds
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
        notional_vst = float(decision.margin_vst) * float(decision.leverage)
        if notional_vst > self.config.max_notional_per_pair_vst:
            return (
                False,
                f"MAX_NOTIONAL_EXCEEDED: notional={notional_vst:.2f} > max_notional_per_pair_vst={self.config.max_notional_per_pair_vst:.2f}",
            )

        # 3. Open positions cap
        if open_positions_count >= self.config.max_open_positions:
            return (
                False,
                f"MAX_OPEN_POSITIONS_REACHED: {open_positions_count} >= {self.config.max_open_positions}",
            )

        # 4. Stop-loss presence, directional validity, and SL-before-liquidation buffer
        ref_price = (
            decision.entry_price
            if (decision.entry_price is not None and decision.entry_price > 0)
            else current_price
        )
        if self.config.require_sl_on_open:
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

        if (
            self.config.enforce_sl_before_liquidation
            and decision.leverage >= 10
            and ref_price > 0
            and decision.sl_price is not None
        ):
            sl_distance_pct = abs(ref_price - decision.sl_price) / ref_price
            max_sl_distance_pct = self.config.liquidation_safety_factor / float(decision.leverage)
            if sl_distance_pct > max_sl_distance_pct:
                return (
                    False,
                    f"SL_BEYOND_LIQUIDATION_BUFFER: sl_dist={sl_distance_pct * 100.0:.2f}% > max_safe={max_sl_distance_pct * 100.0:.2f}% at {decision.leverage}x",
                )

        return True, "OK"
