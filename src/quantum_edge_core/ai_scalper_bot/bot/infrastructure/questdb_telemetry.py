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
    def __init__(self, host: str = '127.0.0.1', port: int = 9009):
        self.host = host
        self.port = port
        self.logger = logging.getLogger("QuestDbTelemetry")

    def _send_payload(self, payload: str):
        """Send ILP payload via raw TCP socket."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0) # Short timeout
            sock.connect((self.host, self.port))
            sock.sendall(payload.encode('utf-8'))
            sock.close()
        except Exception as e:
            self.logger.debug(f"Failed to send telemetry to QuestDB: {e}")

    async def _send_payload_async(self, payload: str):
        """Fire and forget wrapper around the synchronous socket block."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._send_payload, payload)

    def log_portfolio_state(self, symbol: str, equity: float, unrealized_pnl: float, position_qty: float):
        """
        Logs the real-time state of the portfolio.
        Format: portfolio_state,symbol=BTCUSDT equity={equity},unrealized_pnl={pnl},position_qty={qty} <timestamp_ns>
        """
        ts_ns = int(time.time() * 1_000_000_000)
        payload = f"portfolio_state,symbol={symbol} equity={float(equity)},unrealized_pnl={float(unrealized_pnl)},position_qty={float(position_qty)} {ts_ns}\n"
        asyncio.create_task(self._send_payload_async(payload))

    def log_realized_trade(self, symbol: str, side: str, price: float, qty: float, realized_pnl: float):
        """
        Logs a closed or partially closed trade.
        Format: realized_trades,symbol=BTCUSDT side={side} price={price},qty={qty},realized_pnl={pnl} <timestamp_ns>
        """
        ts_ns = int(time.time() * 1_000_000_000)
        payload = f"realized_trades,symbol={symbol},side={side} price={float(price)},qty={float(qty)},realized_pnl={float(realized_pnl)} {ts_ns}\n"
        asyncio.create_task(self._send_payload_async(payload))

