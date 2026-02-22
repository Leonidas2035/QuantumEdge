import sys
from unittest.mock import Mock, patch

sys.modules['binance'] = Mock()
sys.modules['binance.client'] = Mock()
sys.modules['binance.exceptions'] = Mock()

import pytest
from quantum_edge_core.strategies.legacy.lockbot.lockbot_btc.config import LockbotConfig
from quantum_edge_core.strategies.legacy.lockbot.lockbot_btc.main import LockBotService

@pytest.fixture
def service():
    cfg = LockbotConfig()
    # Apply testing configurations
    cfg.ddn.max_margin_usage = 0.5
    cfg.ddn.max_volatility_bps_atr = 150.0
    cfg.ddn.max_band_abs = 0.8
    cfg.ddn.max_step_notional_usd = 100_000.0  # Allow large panic lock tests
    cfg.ddn.profiles["neutral"].band_low = -0.10
    cfg.ddn.profiles["neutral"].band_high = 0.10
    
    # Disable IPC dependencies
    srv = LockBotService(cfg, ipc_enabled=False)
    srv._exec_manager.submit_plans = Mock()
    return srv

def test_margin_guard_blocks_orders(service):
    """Test Margin Guard: Blocks ADD_LONG/ADD_SHORT if margin_usage > 50%"""
    service._market_state.mark_price = 50000.0
    service._market_state.vwap_d = 50000.0
    service._market_state.band_2u = 51000.0
    service._market_state.band_2l = 49000.0
    
    import time
    now = int(time.time() * 1000)
    service._market_state.last_market_ts = now
    service._account_state.last_account_ts = now
    
    # Exceed margin guard
    service._account_state.margin_usage = 0.6
    service._account_state.distance_to_liq_bps = 2000.0
    
    command = {
        "schema": "lockbot_control.v1",
        "msg_type": "cmd",
        "bot_id": "LockBotBTC",
        "symbol": "BTCUSDT",
        "ts_event": now,
        "ts_cmd": now,
        "cmd_id": "test_margin_guard",
        "payload": {
            "cmd": "EXEC_STEP",
            "action": "ADD_LONG",
            "qty_hint": 0.1
        }
    }
    
    ack = service.process_command(command)
    assert ack.payload["status"] == "REJECTED"
    assert ack.payload["error_code"] == "MARGIN_CAP"
    service._exec_manager.submit_plans.assert_not_called()

def test_panic_lock_triggered_by_volatility(service):
    """Test Panic Lock: Triggers 1:1 hedge if volatility exceeds ATR threshold"""
    import time
    now = int(time.time() * 1000)
    service._market_state.last_market_ts = now
    service._account_state.last_account_ts = now
    service._market_state.mark_price = 50000.0
    service._market_state.vwap_d = 50000.0
    
    # Normal margin and liq distance, but huge volatility
    service._account_state.margin_usage = 0.1
    service._account_state.distance_to_liq_bps = 2000.0
    
    # High volatility
    service._market_state.volatility_bps = 200.0 # > 150 config limit
    
    # Net long position 1.5 BTC
    service._account_state.long_qty = 2.0
    service._account_state.short_qty = 0.5
    
    command = {
        "schema": "lockbot_control.v1",
        "msg_type": "cmd",
        "bot_id": "LockBotBTC",
        "symbol": "BTCUSDT",
        "ts_event": now,
        "ts_cmd": now,
        "cmd_id": "test_panic_lock",
        "payload": {
            "cmd": "EXEC_STEP",
            "action": "ADD_LONG",
            "qty_hint": 0.1
        }
    }
    
    ack = service.process_command(command)
    assert ack.payload["status"] == "ACCEPTED"
    
    submit_call = service._exec_manager.submit_plans.call_args
    assert submit_call is not None
    plans = submit_call.kwargs["plans"]
    
    assert len(plans) == 1
    plan = plans[0]
    assert plan.type == "MARKET"
    assert plan.side == "SELL" # Hedge the long
    assert plan.qty == 1.5 # 1:1 neutralize delta

def test_vwap_fading_micro_averaging(service):
    """Test VWAP Micro-averaging: dynamically shifts target inside the channel"""
    import time
    now = int(time.time() * 1000)
    service._market_state.last_market_ts = now
    service._account_state.last_account_ts = now
    # Price is very close to upper band. 
    # vwap = 50000, band_2u = 51000, mark = 50900 -> pos = 0.9.
    service._market_state.mark_price = 50900.0
    service._market_state.vwap_d = 50000.0
    service._market_state.band_2u = 51000.0
    service._market_state.band_2l = 49000.0
    
    service._account_state.margin_usage = 0.1
    service._account_state.distance_to_liq_bps = 2000.0
    service._account_state.long_qty = 0.0
    service._account_state.short_qty = 0.0
    
    command = {
        "schema": "lockbot_control.v1",
        "msg_type": "cmd",
        "bot_id": "LockBotBTC",
        "symbol": "BTCUSDT",
        "ts_event": now,
        "ts_cmd": now,
        "cmd_id": "test_vwap_fade",
        "payload": {
            "cmd": "EXEC_STEP",
            "action": "ADD_SHORT",
            "qty_hint": 0.5
        }
    }
    
    ack = service.process_command(command)
    assert ack.payload["status"] in ["ACCEPTED", "REJECTED"] # May reject due to clamp if qty is too big
    
    # Since mark=50900 pos=0.9, fade target = -0.9 * 0.8 * 0.5 = -0.36.
    # Profile band_low is -0.10. Clamp target to -0.10.
    assert service._bot_state.ddn_target == -0.10
