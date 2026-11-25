"""Execution Bridge, in-memory PaperExchangeClient, and JSONL trade decision journal."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from quantum_edge_core.contracts.lean import (
    FullMarketContext,
    MarketSnapshot,
    OrderResult,
    TradeDecision,
)
from quantum_edge_core.execution.full_market_context import build_full_market_context
from quantum_edge_core.execution.lean_executor import LeanExecutor
from quantum_edge_core.execution.risk_guard import RiskConfig


DEFAULT_JOURNAL_PATH = Path("runtime/lean_trade_journal.jsonl")


class PaperExchangeClient:
    """In-memory CCXT-compatible exchange stub for offline simulation and verification."""

    def __init__(self) -> None:
        self.submitted_orders: list[dict[str, Any]] = []
        self._counter: int = 0

    def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._counter += 1
        order_id = f"paper-{self._counter}"
        copied_params = copy.deepcopy(params) if params is not None else {}
        self.submitted_orders.append(
            {
                "id": order_id,
                "symbol": symbol,
                "type": type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": copied_params,
            }
        )
        return {
            "id": order_id,
            "status": "open",
            "filled": 0.0,
            "average": price,
        }


class HarnessBridge:
    """Clean boundary exposing uncompressed market context and safe order execution to external agents."""

    def __init__(
        self,
        executor: LeanExecutor,
        journal_path: str | Path = DEFAULT_JOURNAL_PATH,
    ) -> None:
        self.executor = executor
        self.journal_path = Path(journal_path)

    def get_full_context(
        self,
        market_snapshot: MarketSnapshot | Mapping[str, Any],
        *,
        ohlcv_by_tf: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        orderbook: Mapping[str, Any] | None = None,
        derivatives: Mapping[str, Any] | None = None,
        microstructure_and_liquidity: Mapping[str, Any] | None = None,
        account_state: Mapping[str, Any] | None = None,
        risk_limits: RiskConfig | Mapping[str, Any] | None = None,
        raw_extras: Mapping[str, Any] | None = None,
    ) -> FullMarketContext:
        effective_risk = (
            risk_limits if risk_limits is not None else self.executor.risk_guard.config
        )
        return build_full_market_context(
            market_snapshot=market_snapshot,
            ohlcv_by_tf=ohlcv_by_tf,
            orderbook=orderbook,
            derivatives=derivatives,
            microstructure_and_liquidity=microstructure_and_liquidity,
            account_state=account_state,
            risk_limits=effective_risk,
            raw_extras=raw_extras,
        )

    def submit_decision(
        self,
        decision: TradeDecision | dict[str, Any],
        current_price: float,
        open_positions_count: int = 0,
        position_qty: float | None = None,
    ) -> OrderResult:
        parsed = (
            decision
            if isinstance(decision, TradeDecision)
            else TradeDecision.model_validate(decision)
        )
        result = self.executor.execute(
            parsed,
            current_price=current_price,
            open_positions_count=open_positions_count,
            position_qty=position_qty,
        )
        self._append_journal(parsed, result)
        return result

    def _append_journal(self, decision: TradeDecision, result: OrderResult) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "decision_id": decision.decision_id,
            "symbol": decision.symbol,
            "action": decision.action,
            "order_type": decision.order_type,
            "entry_price": decision.entry_price,
            "sl_price": decision.sl_price,
            "tp_price": decision.tp_price,
            "margin_vst": decision.margin_vst,
            "leverage": decision.leverage,
            "reason": decision.reason,
            "order_status": result.status,
            "client_order_id": result.client_order_id,
            "exchange_order_id": result.exchange_order_id,
            "error": result.error,
        }
        with self.journal_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
