"""
QuestDB ILP TCP Telemetry Client.
Provides lightweight, non-blocking telemetry logging for the bot.
"""

import socket
import logging
import time
import asyncio
from typing import Optional


class QuestDbTelemetry:
    def __init__(self, host: str = "127.0.0.1", port: int = 9009):
        self.host = host
        self.port = port
        self.logger = logging.getLogger("QuestDbTelemetry")

    def _send_payload(self, payload: str):
        """Send ILP payload via raw TCP socket."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)  # Short timeout
            sock.connect((self.host, self.port))
            sock.sendall(payload.encode("utf-8"))
            sock.close()
        except Exception as e:
            self.logger.error(
                f"QuestDB write failed: {e} — continuing with memory balance"
            )

    async def _send_payload_async(self, payload: str):
        """Fire and forget wrapper around the synchronous socket block."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_payload, payload)

    def log_portfolio_state(
        self, symbol: str, equity: float, unrealized_pnl: float, position_qty: float, leverage: float = 0.0, liquidation_price: float = 0.0
    ):
        """
        Logs the real-time state of the portfolio.
        Format: portfolio_state,symbol=BTCUSDT equity={equity},unrealized_pnl={pnl},position_qty={qty},leverage={leverage},liquidation_price={liquidation_price} <timestamp_ns>
        """
        ts_ns = int(time.time() * 1_000_000_000)
        payload = f"portfolio_state,symbol={symbol} equity={float(equity)},unrealized_pnl={float(unrealized_pnl)},position_qty={float(position_qty)},leverage={float(leverage)},liquidation_price={float(liquidation_price)} {ts_ns}\n"
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._send_payload_async(payload))
        except RuntimeError:
            self._send_payload(payload)

    def log_realized_trade(
        self, symbol: str, side: str, entry_price: float, exit_price: float, qty: float, realized_pnl: float, fees: float = 0.0
    ):
        """
        Logs a closed or partially closed trade via ILP Writer.
        """
        import os
        bot_id = os.getenv("QE_BOT_ID", "ai_scalper_bot")
        ts = time.time()
        
        try:
            from quantum_edge_core.market_data.tsdb.ilp_writer import get_ilp_writer
            writer = get_ilp_writer()
            writer.write_row(
                "realized_trades",
                symbols={"bot_id": bot_id, "symbol": symbol, "side": side},
                columns={
                    "entry_price": float(entry_price),
                    "exit_price": float(exit_price),
                    "qty": float(qty),
                    "realized_pnl": float(realized_pnl),
                    "fees": float(fees)
                },
                ts=ts
            )
            self.logger.info(f"Position Closed [{bot_id}]: {side} {qty} {symbol} | PnL: ${realized_pnl:.2f}")
        except Exception as e:
            self.logger.error(f"Failed to log realized_trade: {e}")
