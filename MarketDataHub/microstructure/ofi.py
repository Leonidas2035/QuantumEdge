"""Order-flow imbalance (OFI) analyzer for top-of-book."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional
import time


@dataclass
class MicrostructureSnapshot:
    ts_event: int
    ts_ingest: int
    symbol: str
    best_bid_px: float
    best_bid_qty: float
    best_ask_px: float
    best_ask_qty: float
    ofi_raw: float
    ofi_z: float
    ofi_ma5: float
    spread_bps: float
    top_qty_sum: float
    trade_rate_1s: Optional[float]
    volume_1s: Optional[float]
    is_gap: bool
    is_resynced: bool


class MicrostructureAnalyzer:
    """Computes OFI + companion microstructure features with rolling stats."""

    def __init__(self, window_n: int = 50, eps: float = 1e-9, trade_window_sec: float = 1.0) -> None:
        self._window_n = max(int(window_n), 5)
        self._eps = float(eps)
        self._trade_window_ns = max(float(trade_window_sec), 0.1) * 1_000_000_000
        self._ofi_window: Deque[float] = deque(maxlen=self._window_n)
        self._ofi_z_window: Deque[float] = deque(maxlen=max(self._window_n, 5))
        self._trade_window: Deque[tuple[int, float]] = deque()
        self._prev_bid_px: Optional[float] = None
        self._prev_bid_qty: Optional[float] = None
        self._prev_ask_px: Optional[float] = None
        self._prev_ask_qty: Optional[float] = None
        self._gap_flag = False
        self._resync_flag = False

    def mark_gap(self) -> None:
        """Mark an input gap and reset rolling stats."""
        self._gap_flag = True
        self._reset()

    def mark_resync(self) -> None:
        """Mark a book resync and reset rolling stats."""
        self._resync_flag = True
        self._reset()

    def update_trade(self, ts_ns: int, qty: float) -> None:
        if ts_ns <= 0:
            return
        qty_val = float(qty)
        if qty_val <= 0:
            return
        self._trade_window.append((ts_ns, qty_val))
        self._drain_trades(ts_ns)

    def update_book(
        self,
        *,
        symbol: str,
        bid_px: float,
        bid_qty: float,
        ask_px: float,
        ask_qty: float,
        ts_event: int,
    ) -> Optional[MicrostructureSnapshot]:
        if bid_px <= 0 or ask_px <= 0:
            return None
        if ask_px <= bid_px:
            return None
        ofi_raw = self._compute_ofi(bid_px, bid_qty, ask_px, ask_qty)
        self._ofi_window.append(ofi_raw)
        mean = sum(self._ofi_window) / max(len(self._ofi_window), 1)
        var = sum((val - mean) ** 2 for val in self._ofi_window) / max(len(self._ofi_window), 1)
        std = max(var ** 0.5, self._eps)
        ofi_z = (ofi_raw - mean) / std
        self._ofi_z_window.append(ofi_z)
        window = list(self._ofi_z_window)[-5:]
        ofi_ma5 = sum(window) / max(len(window), 1)

        mid = (bid_px + ask_px) / 2.0
        spread_bps = ((ask_px - bid_px) / mid * 10_000.0) if mid > 0 else 0.0
        top_qty_sum = bid_qty + ask_qty
        trade_rate, volume = self._trade_stats(ts_event)

        ts_ingest = time.time_ns()
        snapshot = MicrostructureSnapshot(
            ts_event=ts_event,
            ts_ingest=ts_ingest,
            symbol=symbol,
            best_bid_px=bid_px,
            best_bid_qty=bid_qty,
            best_ask_px=ask_px,
            best_ask_qty=ask_qty,
            ofi_raw=ofi_raw,
            ofi_z=ofi_z,
            ofi_ma5=ofi_ma5,
            spread_bps=spread_bps,
            top_qty_sum=top_qty_sum,
            trade_rate_1s=trade_rate,
            volume_1s=volume,
            is_gap=self._consume_gap_flag(),
            is_resynced=self._consume_resync_flag(),
        )
        return snapshot

    def _compute_ofi(self, bid_px: float, bid_qty: float, ask_px: float, ask_qty: float) -> float:
        if self._prev_bid_px is None or self._prev_ask_px is None:
            self._prev_bid_px = bid_px
            self._prev_bid_qty = bid_qty
            self._prev_ask_px = ask_px
            self._prev_ask_qty = ask_qty
            return 0.0

        prev_bid_px = self._prev_bid_px
        prev_bid_qty = self._prev_bid_qty or 0.0
        prev_ask_px = self._prev_ask_px
        prev_ask_qty = self._prev_ask_qty or 0.0

        if bid_px > prev_bid_px:
            bid_component = bid_qty
        elif bid_px < prev_bid_px:
            bid_component = -prev_bid_qty
        else:
            bid_component = bid_qty - prev_bid_qty

        if ask_px < prev_ask_px:
            ask_component = ask_qty
        elif ask_px > prev_ask_px:
            ask_component = -prev_ask_qty
        else:
            ask_component = -(ask_qty - prev_ask_qty)

        self._prev_bid_px = bid_px
        self._prev_bid_qty = bid_qty
        self._prev_ask_px = ask_px
        self._prev_ask_qty = ask_qty
        return float(bid_component + ask_component)

    def _trade_stats(self, ts_event: int) -> tuple[Optional[float], Optional[float]]:
        if ts_event <= 0:
            return None, None
        self._drain_trades(ts_event)
        if not self._trade_window:
            return None, None
        count = len(self._trade_window)
        volume = sum(qty for _, qty in self._trade_window)
        return float(count), float(volume)

    def _drain_trades(self, ts_ns: int) -> None:
        cutoff = ts_ns - self._trade_window_ns
        while self._trade_window and self._trade_window[0][0] < cutoff:
            self._trade_window.popleft()

    def _reset(self) -> None:
        self._ofi_window.clear()
        self._ofi_z_window.clear()
        self._trade_window.clear()
        self._prev_bid_px = None
        self._prev_bid_qty = None
        self._prev_ask_px = None
        self._prev_ask_qty = None

    def _consume_gap_flag(self) -> bool:
        if self._gap_flag:
            self._gap_flag = False
            return True
        return False

    def _consume_resync_flag(self) -> bool:
        if self._resync_flag:
            self._resync_flag = False
            return True
        return False
