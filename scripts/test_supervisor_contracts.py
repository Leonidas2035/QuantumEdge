#!/usr/bin/env python3
"""Supervisor Cold Path — Contract Integration Test.

Tests the full chain:
  Mock Telemetry → Signal Aggregation → Gemini LLM Call
    → JSON Parse → Policy Schema Validation

Run:
    PYTHONPATH=src python scripts/test_supervisor_contracts.py

Requires: GOOGLE_API_KEY (env or config/config.yaml)
"""

import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

# Ensure supervisor subpackages are importable
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent
        / "src"
        / "quantum_edge_core"
        / "supervisor"
    ),
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from supervisor.config import (
    LlmSupervisorConfig,
    LlmSupervisorTrustPolicy,
    CircuitBreakerConfig,
    RiskConfig,
)
from supervisor.state import RiskStateSnapshot
from supervisor.llm_supervisor import (
    LlmSupervisor,
    LlmSupervisorAdvice,
    LlmSupervisorSummary,
    LlmAction,
    build_summary,
    build_prompts,
)
from supervisor.llm.google_client import GoogleClient
from policy.policy_contract import Policy, POLICY_VERSION, ALLOWED_MODES

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("ContractTest")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Mock Data
# ═══════════════════════════════════════════════════════════════════════════════


def build_mock_telemetry() -> dict:
    """Simulate a healthy bot telemetry payload."""
    return {
        "source": "ai_scalper_bot",
        "timestamp": time.time(),
        "status": "IDLE",
        "pnl_session": 12.50,
        "drawdown_pct": 0.8,
        "metrics": {
            "active_positions_count": 0,
            "cpu_usage": 3.2,
        },
        "errors": [],
    }


def build_mock_risk_snapshot() -> RiskStateSnapshot:
    """Simulate a healthy risk state."""
    return RiskStateSnapshot(
        trading_day=date.today(),
        equity_start=10_000.0,
        equity_now=10_012.50,
        realized_pnl_today=12.50,
        max_equity_intraday=10_015.00,
        min_equity_intraday=9_995.00,
        halted=False,
        halt_reason=None,
        llm_risk_multiplier=1.0,
        llm_paused=False,
    )


def build_mock_risk_config() -> RiskConfig:
    """Simulate standard risk limits."""
    return RiskConfig(
        currency="USDT",
        max_daily_loss_abs=500.0,
        max_daily_loss_pct=5.0,
        max_drawdown_abs=200.0,
        max_drawdown_pct=2.0,
        max_notional_per_symbol=50_000.0,
        max_leverage=5.0,
    )


def build_mock_llm_config() -> LlmSupervisorConfig:
    """Supervisor LLM config (will skip min_order_decisions)."""
    return LlmSupervisorConfig(
        enabled=True,
        model="gemini-2.5-flash",
        api_url="",  # not used by GoogleClient
        api_key_env="GOOGLE_API_KEY",
        check_interval_minutes=5,
        timeout_seconds=30,
        min_order_decisions=0,  # Skip the event count guard
        max_events_in_summary=50,
        max_trades_in_table=10,
        dry_run=True,
        trust_policy=LlmSupervisorTrustPolicy(
            allow_risk_multiplier=True,
            allow_mode_switch=True,
            allow_pause=True,
            min_multiplier=0.1,
            max_multiplier=2.0,
        ),
        circuit_breaker=CircuitBreakerConfig(
            failures=3,
            window_sec=60,
            open_sec=120,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Test Runner
# ═══════════════════════════════════════════════════════════════════════════════


def main() -> int:
    errors = 0

    # ── STEP 1: Mock Telemetry ────────────────────────────────────────────
    telemetry = build_mock_telemetry()
    print("\n" + "=" * 60)
    print("--- MOCK TELEMETRY ---")
    print("=" * 60)
    print(json.dumps(telemetry, indent=2))

    # ── STEP 2: Build LLM Summary from mock risk state ───────────────────
    snapshot = build_mock_risk_snapshot()
    risk_cfg = build_mock_risk_config()
    llm_cfg = build_mock_llm_config()

    summary = build_summary(
        snapshot=snapshot,
        limits=risk_cfg,
        events=[],  # no events for this test
        config=llm_cfg,
        mode="scalp",
    )
    system_prompt, user_prompt = build_prompts(summary, risk_cfg, llm_cfg)

    print("\n" + "=" * 60)
    print("--- LLM PROMPT (user) ---")
    print("=" * 60)
    print(user_prompt[:500])

    # ── STEP 3: Call Gemini LLM ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print("--- CALLING GEMINI LLM ---")
    print("=" * 60)

    try:
        client = GoogleClient()
        llm_supervisor = LlmSupervisor(
            config=llm_cfg,
            risk_config=risk_cfg,
            events_dir=Path("/tmp/qe_test_events"),
            logger=logger,
            chat_client=client,
        )
        raw_response = llm_supervisor.call_llm(system_prompt, user_prompt)
    except Exception as exc:
        print(f"❌ LLM CALL FAILED: {exc}")
        return 1

    print("\n" + "=" * 60)
    print("--- RAW LLM RESPONSE ---")
    print("=" * 60)
    print(raw_response)

    # ── STEP 4: Parse LLM Advice ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("--- PARSED LLM ADVICE ---")
    print("=" * 60)

    advice = llm_supervisor.parse_advice(raw_response)
    print(f"  action:          {advice.action.value}")
    print(f"  risk_multiplier: {advice.risk_multiplier}")
    print(f"  comment:         {advice.comment}")

    if advice.action == LlmAction.UNSPECIFIED:
        print("⚠️  WARNING: LLM returned UNSPECIFIED action (parse may have failed)")
        errors += 1
    else:
        print("✅ LLM Advice schema validated OK")

    # ── STEP 5: Validate Policy Schema ───────────────────────────────────
    print("\n" + "=" * 60)
    print("--- PARSED POLICY (VALIDATED) ---")
    print("=" * 60)

    try:
        policy = Policy(
            version=POLICY_VERSION,
            ts=int(time.time()),
            ttl_sec=30,
            allow_trading=True,
            mode="normal",
            size_multiplier=advice.risk_multiplier if advice.risk_multiplier else 1.0,
            cooldown_sec=0,
            spread_max_bps=25.0,
            max_daily_loss=500.0,
            reason=f"LLM:{advice.action.value}",
        )
        print(policy.to_json(pretty=True))
        print("✅ Policy schema validated OK")
    except (ValueError, TypeError) as exc:
        print(f"❌ POLICY VALIDATION FAILED: {exc}")
        errors += 1

    # ── STEP 6: Round-trip test (serialize → deserialize → validate) ────
    print("\n" + "=" * 60)
    print("--- POLICY ROUND-TRIP TEST ---")
    print("=" * 60)

    try:
        policy_dict = policy.to_dict()
        policy_json = json.dumps(policy_dict)
        policy_parsed = json.loads(policy_json)
        policy_restored = Policy.from_dict(policy_parsed)
        assert policy_restored.version == policy.version
        assert policy_restored.allow_trading == policy.allow_trading
        assert policy_restored.mode == policy.mode
        print(f"  Round-trip: {policy.version} → JSON → from_dict ✅")
    except Exception as exc:
        print(f"❌ ROUND-TRIP FAILED: {exc}")
        errors += 1

    # ── STEP 7: Negative test — invalid policy ──────────────────────────
    print("\n" + "=" * 60)
    print("--- NEGATIVE TEST: INVALID POLICY ---")
    print("=" * 60)

    try:
        Policy(version="", ts=-1, ttl_sec=0, allow_trading=True, mode="invalid")
        print("❌ Should have raised ValueError for invalid policy!")
        errors += 1
    except ValueError as exc:
        print(f"  Correctly rejected: {exc} ✅")

    # ── Summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if errors == 0:
        print("🎉 ALL CONTRACT TESTS PASSED")
    else:
        print(f"⚠️  {errors} ERROR(S) DETECTED")
    print("=" * 60)

    return errors


if __name__ == "__main__":
    sys.exit(main())
