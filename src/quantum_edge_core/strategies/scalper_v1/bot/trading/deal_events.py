"""Deal-closed event emission helpers (Stage 9.4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Set

EmitFn = Callable[[str, Dict[str, object], Optional[str]], None]


@dataclass
class DealEventEmitter:
    emit_fn: EmitFn

    def emit(
        self, event_type: str, payload: Dict[str, object], symbol: Optional[str]
    ) -> None:
        self.emit_fn(event_type, payload, symbol)


@dataclass
class DcaDealTracker:
    emitter: DealEventEmitter
    seen_lots: Set[str] = field(default_factory=set)

    def record_lot_closed(
        self,
        *,
        strategy_id: str,
        symbol: str,
        lot_id: str,
        pnl: float,
        fees: float = 0.0,
        volume_quote: float = 0.0,
        ts_ms: Optional[int] = None,
    ) -> bool:
        deal_id = f"{strategy_id}:{lot_id}"
        if deal_id in self.seen_lots:
            return False
        self.seen_lots.add(deal_id)
        payload = {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "deal_id": deal_id,
            "lot_id": lot_id,
            "net_pnl": pnl,
            "fees": fees,
            "gross_pnl": pnl + fees,
            "volume_quote": volume_quote,
            "ts_ms": ts_ms,
        }
        self.emitter.emit("dca_deal_closed.v1", payload, symbol)
        return True


@dataclass
class ScalpDealTracker:
    emitter: DealEventEmitter
    seen_cycles: Set[str] = field(default_factory=set)

    def record_cycle_closed(
        self,
        *,
        strategy_id: str,
        symbol: str,
        cycle_id: str,
        pnl: float,
        fees: float = 0.0,
        volume_quote: float = 0.0,
        ts_ms: Optional[int] = None,
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
        qty: Optional[float] = None,
    ) -> bool:
        deal_id = f"{strategy_id}:{cycle_id}"
        if deal_id in self.seen_cycles:
            return False
        self.seen_cycles.add(deal_id)
        payload = {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "deal_id": deal_id,
            "cycle_id": cycle_id,
            "net_pnl": pnl,
            "fees": fees,
            "gross_pnl": pnl + fees,
            "volume_quote": volume_quote,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "qty": qty,
            "ts_ms": ts_ms,
        }
        self.emitter.emit("scalp_deal_closed.v1", payload, symbol)
        return True
