import asyncio
import logging
from quantum_edge_core.supervisor.supervisor.market_context_builder import MarketContextBuilder
from quantum_edge_core.supervisor.supervisor.state import RiskStateSnapshot

logging.basicConfig(level=logging.INFO)

async def test():
    builder = MarketContextBuilder()
    mock_state = RiskStateSnapshot(
        trading_day="test",
        equity_start=10000.0,
        equity_now=10500.0,
        realized_pnl_today=500.0,
        max_equity_intraday=10500.0,
        min_equity_intraday=10000.0,
        halted=False,
        halt_reason=""
    )
    res = await builder.build_context("BTCUSDT", mock_state)
    print("OUTPUT JSON:", res)

if __name__ == "__main__":
    asyncio.run(test())
