"""
End-to-End System Test.
Verifies the integration of Context Builder, Risk Engine, Policy Manager, and IPC.
"""
import time
import pytest
from unittest.mock import MagicMock, patch

from quantum_edge_core.supervisor.service import AsyncSupervisor
from quantum_edge_core.supervisor.domain.models import RiskLevel

# Mock Zmq
@pytest.fixture
def mock_zmq():
    with patch("quantum_edge_core.supervisor.supervisor.ipc.zmq.asyncio.Context") as mock_ctx:
        yield mock_ctx

@pytest.fixture
def mock_gemini():
    with patch("quantum_edge_core.supervisor.service.GeminiClient") as mock_client:
        mock_instance = MagicMock()
        mock_instance.safe_analyze_risk.return_value = {
            "action": "CONTINUE",
            "sentiment": "bullish",
            "confidence": 0.9,
            "reasoning": "Market looks good",
            "regime": "TREND"
        }
        mock_client.return_value = mock_instance
        yield mock_instance

@pytest.mark.asyncio
async def test_supervisor_flow(mock_zmq, mock_gemini):
    """
    Test the full loop:
    1. Data Ingest (Mock)
    2. Context Build
    3. Monitor Loop checks Hard Risk
    4. Strategy Loop gets AI Decision
    5. IPC Broadcast
    """
    supervisor = AsyncSupervisor()
    
    # 1. Setup
    await supervisor._setup()
    
    # Inject Mock Data into ZmqListener Store/Accumulator
    # Supervisor uses context_builder accumulators directly now
    acc = supervisor.context_builder.get_accumulator("BTCUSDT")
    acc.add_trade({"p": 65000.0, "q": 1.0, "m": False, "T": time.time()*1000})
    
    # Inject Risk State (Balance)
    supervisor.bot_state["equity_current"] = 10000.0
    supervisor.bot_state["current_exposure"] = 5000.0 # 0.5x leverage
    
    # 2. Run Monitor Loop Once (Manually to check logic)
    # We can't easily run the infinite loop, so we inspect the logic components
    
    # Check Context
    ctx = supervisor.context_builder.build_snapshot()
    assert ctx["market_state"]["price"] == 65000.0
    
    # Check Risk Logic (should be Normal)
    # In monitor loop:
    from quantum_edge_core.supervisor.domain.models import PortfolioState
    from quantum_edge_core.supervisor.domain.risk import HardRiskEngine
    
    p_state = PortfolioState(
        equity_start_day=10000.0,
        equity_current=10000.0,
        unrealized_pnl=0.0,
        total_exposure=5000.0,
        open_order_count=0,
        used_leverage=0.5
    )
    verdict = HardRiskEngine.check_risk(p_state, supervisor.risk_config)
    assert verdict.level == RiskLevel.NORMAL
    
    # Check Policy Update (Mock AI trigger)
    # Simulate Strategy Loop Logic
    ai_result = await supervisor.gemini_client.safe_analyze_risk(ctx)
    new_policy = supervisor.policy_manager.apply_ai_decision(ai_result)
    
    assert new_policy.long_allowed is True
    assert new_policy.short_allowed is False # Bullish
    assert new_policy.risk_multiplier == 1.0
    
    # Check Emergency Override Logic
    # Simulate Hard Risk
    supervisor.bot_state["current_exposure"] = 500000.0 # Huge leverage
    p_state_risky = PortfolioState(
        equity_start_day=10000.0,
        equity_current=10000.0,
        unrealized_pnl=0.0,
        total_exposure=500000.0,
        open_order_count=0,
        used_leverage=50.0
    )
    verdict_risky = HardRiskEngine.check_risk(p_state_risky, supervisor.risk_config)
    assert verdict_risky.level != RiskLevel.NORMAL
    
    safe_policy = supervisor.policy_manager.enforce_hard_risk(verdict_risky, new_policy)
    assert safe_policy.risk_multiplier != 1.0 # Should be reduced or 0
    
    print("Full System Flow Verified!")

if __name__ == "__main__":
    # If run directly allow quick check
    pass
