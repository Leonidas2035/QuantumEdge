"""LockAuditor — startup position/margin diagnostics.

Queries Binance Futures for current account state before the DDN engine
begins processing ticks. Determines if the bot is FLAT, LOCKED, or
IMBALANCED and populates the initial AccountState.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AuditResult:
    """Snapshot of exchange state at startup."""

    # Account
    total_wallet_balance: float = 0.0
    total_margin_balance: float = 0.0
    available_balance: float = 0.0
    margin_usage_pct: float = 0.0

    # Positions
    long_qty: float = 0.0
    short_qty: float = 0.0
    long_entry_price: Optional[float] = None
    short_entry_price: Optional[float] = None
    long_liq_price: Optional[float] = None
    short_liq_price: Optional[float] = None
    unrealized_pnl: float = 0.0

    # Derived
    net_delta: float = 0.0
    status: str = "FLAT"  # FLAT | LOCKED | IMBALANCED

    def as_dict(self) -> Dict:
        return {
            "total_wallet_balance": self.total_wallet_balance,
            "total_margin_balance": self.total_margin_balance,
            "available_balance": self.available_balance,
            "margin_usage_pct": round(self.margin_usage_pct, 2),
            "long_qty": self.long_qty,
            "short_qty": self.short_qty,
            "long_entry_price": self.long_entry_price,
            "short_entry_price": self.short_entry_price,
            "long_liq_price": self.long_liq_price,
            "short_liq_price": self.short_liq_price,
            "unrealized_pnl": round(self.unrealized_pnl, 4),
            "net_delta": round(self.net_delta, 6),
            "status": self.status,
        }


class LockAuditor:
    """Queries Binance Futures for account/position state at startup."""

    def __init__(
        self,
        api_key_env: str = "BINANCE_TESTNET_API_KEY",
        api_secret_env: str = "BINANCE_TESTNET_API_SECRET",
    ) -> None:
        self._api_key_env = api_key_env
        self._api_secret_env = api_secret_env

    def run_audit(self, symbol: str = "BTCUSDT") -> AuditResult:
        """Fetch account + positions from Binance Futures. Returns AuditResult."""
        result = AuditResult()

        api_key = os.getenv(self._api_key_env)
        api_secret = os.getenv(self._api_secret_env)
        if not api_key or not api_secret:
            logger.warning(
                "[LockAuditor] API keys not set (%s / %s). "
                "Running in OFFLINE mode with default FLAT state.",
                self._api_key_env,
                self._api_secret_env,
            )
            return result

        try:
            from binance.client import Client

            client = Client(api_key, api_secret, testnet=True)
            client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
            client.FUTURES_TESTNET_URL = "https://testnet.binancefuture.com/fapi"
        except Exception as exc:
            logger.error("[LockAuditor] Failed to create Binance client: %s", exc)
            return result

        # ── 1. Account balance ───────────────────────────────────────
        try:
            account = client.futures_account()
            result.total_wallet_balance = float(account.get("totalWalletBalance", 0))
            result.total_margin_balance = float(account.get("totalMarginBalance", 0))
            result.available_balance = float(account.get("availableBalance", 0))
            if result.total_margin_balance > 0:
                used = result.total_margin_balance - result.available_balance
                result.margin_usage_pct = (used / result.total_margin_balance) * 100.0
        except Exception as exc:
            logger.error("[LockAuditor] Failed to fetch account: %s", exc)

        # ── 2. Positions ─────────────────────────────────────────────
        try:
            positions: List[Dict] = client.futures_position_information(symbol=symbol)
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                upnl = float(pos.get("unRealizedProfit", 0))
                entry = float(pos.get("entryPrice", 0))
                liq = float(pos.get("liquidationPrice", 0))

                if amt > 0:
                    result.long_qty = abs(amt)
                    result.long_entry_price = entry if entry > 0 else None
                    result.long_liq_price = liq if liq > 0 else None
                    result.unrealized_pnl += upnl
                elif amt < 0:
                    result.short_qty = abs(amt)
                    result.short_entry_price = entry if entry > 0 else None
                    result.short_liq_price = liq if liq > 0 else None
                    result.unrealized_pnl += upnl
        except Exception as exc:
            logger.error("[LockAuditor] Failed to fetch positions: %s", exc)

        # ── 3. Derived state ─────────────────────────────────────────
        result.net_delta = result.long_qty - result.short_qty

        if result.long_qty == 0 and result.short_qty == 0:
            result.status = "FLAT"
        elif abs(result.net_delta) < 1e-8 and result.long_qty > 0:
            result.status = "LOCKED"
        else:
            result.status = "IMBALANCED"

        return result

    @staticmethod
    def log_audit(result: AuditResult) -> None:
        """Pretty-print audit results to console."""
        logger.info(
            "\n┌─── LockBot Startup Audit ─────────────────────────────────┐"
            "\n│ Balance : %.2f USDT  |  Margin Used : %.1f%%"
            "\n│ LONG    : %.6f BTC  |  Entry: %s  |  Liq: %s"
            "\n│ SHORT   : %.6f BTC  |  Entry: %s  |  Liq: %s"
            "\n│ Net Δ   : %.6f     |  U-PnL: %.4f USDT"
            "\n│ Status  : %s"
            "\n└───────────────────────────────────────────────────────────┘",
            result.total_wallet_balance,
            result.margin_usage_pct,
            result.long_qty,
            f"{result.long_entry_price:.2f}" if result.long_entry_price else "—",
            f"{result.long_liq_price:.2f}" if result.long_liq_price else "—",
            result.short_qty,
            f"{result.short_entry_price:.2f}" if result.short_entry_price else "—",
            f"{result.short_liq_price:.2f}" if result.short_liq_price else "—",
            result.net_delta,
            result.unrealized_pnl,
            result.status,
        )
