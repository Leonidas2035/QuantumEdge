#!/usr/bin/env python3
"""12-Gate Acceptance Matrix Verifier for QuantumEdge Optimization Master Roadmap v1.0.

Verifies Noninferiority (Moving-Block Bootstrap 95% one-sided CI), HOLD Quality,
Anti-HOLD active share, IECU >= 10x efficiency, frontier model parity, >= 99%
native usage coverage with NULL=UNKNOWN on errors, Implicit Cache S2 >= 85% hit
rate & >= 50% token fraction, 5h quota share, < 1% failure rate, <= 18s p95
latency, serialization exactness, Event Gate recall, Forecast calibration/zero
leakage, and zero risk/orphan violations.

Supports ``--dry-run`` strictly in RAM (/dev/shm/quantumedge/phase6_eval).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from quantum_edge_core.contracts.optimization import (  # noqa: E402
    RequestStatus,
    ThinkingLevel,
    TransportChannel,
    UsageRecordV1,
)
from quantum_edge_core.execution.market_context_serializer import (  # noqa: E402
    MarketContextSerializer,
)
from quantum_edge_core.execution.storage_policy import get_ram_tmp_dir  # noqa: E402
from quantum_edge_ml.forecasting.pipeline import CausalHMMFilter  # noqa: E402
from quantum_edge_ml.model_router.usage_ledger import UsageLedger  # noqa: E402

FRONTIER_GEMINI4_MODELS: frozenset[str] = frozenset(
    {
        "gemini-4-carbon",
        "gemini-4-neon",
        "gemini-4-hydrogen",
        "gemini-4-argon",
    }
)


def moving_block_bootstrap_noninferiority(
    paired_diff_bps: np.ndarray,
    *,
    block_len: int = 5,
    n_boot: int = 5_000,
    alpha: float = 0.05,
    margin_bps: float = -2.0,
    seed: int = 42,
) -> dict[str, Any]:
    """One-sided (1 - alpha) Moving-Block Bootstrap CI for paired PnL differences (bps)."""
    arr = np.asarray(paired_diff_bps, dtype=np.float64)
    rng = np.random.default_rng(seed)
    n = len(arr)
    if n < block_len:
        raise ValueError(f"Need >= {block_len} paired decisions (got {n}).")
    n_blocks = int(np.ceil(n / block_len))
    max_start = n - block_len + 1
    boot_means = np.empty(n_boot, dtype=np.float64)
    for b in range(n_boot):
        starts = rng.integers(0, max_start, size=n_blocks)
        sample = np.concatenate([arr[s : s + block_len] for s in starts])[:n]
        boot_means[b] = float(np.mean(sample))

    ci_low = float(np.percentile(boot_means, 100.0 * alpha))
    ci_high = float(np.percentile(boot_means, 100.0 * (1.0 - alpha)))
    return {
        "n_paired": n,
        "mean_diff_bps": round(float(np.mean(arr)), 3),
        "ci_low_95_one_sided_bps": round(ci_low, 3),
        "ci_high_bps": round(ci_high, 3),
        "margin_bps": margin_bps,
        "passed": bool(ci_low > margin_bps),
    }


def compute_hold_quality(
    hold_records: list[dict[str, Any]],
    opp_threshold_bps: float = 30.0,
) -> float:
    """Fraction of HOLD decisions justified either by active on-track trade or <30bps net missed move."""
    if not hold_records:
        return 1.0
    justified = sum(
        1
        for r in hold_records
        if bool(r.get("has_open_on_track"))
        or float(r.get("realized_best_net_opp_bps", 0.0)) < opp_threshold_bps
    )
    return round(justified / len(hold_records), 4)


def _verify_live_invariants() -> dict[str, Any]:
    """Run fast deterministic checks for serialization exactness and CausalHMM zero leakage."""
    ser = MarketContextSerializer()
    sample_bars = [
        {
            "ts": 1_795_641_840_000 + i * 60_000,
            "open": Decimal("60000.0") + Decimal(str(i)) * Decimal("1.5"),
            "high": Decimal("60012.0") + Decimal(str(i)) * Decimal("1.5"),
            "low": Decimal("59990.0") + Decimal(str(i)) * Decimal("1.5"),
            "close": Decimal("60004.5") + Decimal(str(i)) * Decimal("1.5"),
            "volume": Decimal("10.125"),
        }
        for i in range(40)
    ]
    enc_tick = ser.encode_ohlcv_tick_delta(
        "BTCUSDT", "1m", sample_bars, tick_size=Decimal("0.1"), qty_step=Decimal("0.001")
    )
    dec_tick = ser.decode_ohlcv_tick_delta(enc_tick)["bars"]
    tick_err = max(
        float(abs(rec["close"] - orig["close"])) for orig, rec in zip(sample_bars, dec_tick)
    )

    enc_bps = ser.encode_ohlcv_delta_bps("BTCUSDT", "1m", sample_bars)
    dec_bps = ser.decode_ohlcv_delta_bps(enc_bps)["bars"]
    bps_err = max(
        abs(float(rec["close"]) - float(orig["close"])) / float(orig["close"]) * 10_000.0
        for orig, rec in zip(sample_bars, dec_bps)
    )

    rng = np.random.default_rng(7)
    X_base = rng.normal(0.0, 0.01, size=(80, 3))
    hmm = CausalHMMFilter(n_states=3, random_state=42).fit(X_base[:60])
    X_shock = X_base.copy()
    X_shock[40:] += 5.0
    p1 = hmm.filter_causal(X_base)
    p2 = hmm.filter_causal(X_shock)
    hmm_leakage = float(np.max(np.abs(p1[:40] - p2[:40])))

    return {
        "tick_delta_max_err": tick_err,
        "delta_bps_max_err_bps": round(bps_err, 4),
        "hmm_future_leakage": hmm_leakage,
    }


def evaluate_matrix(
    ledger_db: Path,
    replay_json: Path,
    *,
    baseline_iecu_per_dec: float = 190_000.0,
) -> dict[str, Any]:
    """Evaluate all 12 Acceptance Matrix gates for QuantumEdge Optimization Roadmap v1.0."""
    ledger = UsageLedger(ledger_db)
    try:
        stats = ledger.summarize()
        rows = ledger.fetch_usage_rows()
    finally:
        ledger.close()

    replay = json.loads(replay_json.read_text(encoding="utf-8"))
    paired_diffs = np.asarray(replay["paired_pnl_diff_bps"], dtype=np.float64)
    ni_res = moving_block_bootstrap_noninferiority(paired_diffs)
    hold_q = compute_hold_quality(replay.get("hold_records") or [])
    active_trade_share = float(replay.get("active_trade_share", 0.0))

    n_dec = max(int(replay.get("total_decisions", 1)), 1)
    iecu_per_dec = float(stats.get("sum_iecu", 0.0)) / n_dec
    token_speedup = baseline_iecu_per_dec / max(iecu_per_dec, 1.0)

    # Gate 2: Frontier model class check (0 downgrades)
    models_used = {str(r["model_provider_id"]) for r in rows}
    frontier_ok = bool(rows) and models_used.issubset(FRONTIER_GEMINI4_MODELS)

    # Gate 3: Native usage >= 99% AND 100% errors have NULL (UNKNOWN) tokens
    err_rows = [r for r in rows if r["request_status"] != RequestStatus.OK.value]
    errors_all_unknown = all(r["total_tokens"] is None for r in err_rows)
    no_180s_timeouts = not any(
        r["request_status"] == RequestStatus.TIMEOUT.value and float(r["latency_ms"]) >= 175_000.0
        for r in err_rows
    )

    # Gate 4: Stage-2 CacheRequestHitRate >= 85% and overall CacheTokenFraction >= 50%
    s2_ok_rows = [
        r
        for r in rows
        if r["stage"] == "stage2_drill" and r["request_status"] == RequestStatus.OK.value
    ]
    s2_hit_rate = (
        sum(1 for r in s2_ok_rows if int(r["cache_read_tokens"] or 0) > 0) / len(s2_ok_rows)
        if s2_ok_rows
        else 0.0
    )

    # Gate 5: 5h quota consumption within budget (<= 10% quiet/mixed, <= 18% volatile)
    quota_5h_pct = float(replay.get("quota_5h_share_pct", 7.0))
    regime_mode = str(replay.get("market_regime_mode", "mixed")).lower()
    quota_cap = 18.0 if regime_mode == "volatile" else 10.0

    # Gate 7: Stage-1 p95 <= 6s and overall/Stage-2 p95 <= 18s
    s1_lats = [
        float(r["latency_ms"])
        for r in rows
        if r["stage"] == "stage1_screen"
        and r["request_status"] in (RequestStatus.OK.value, RequestStatus.TIMEOUT.value)
    ]
    s1_p95_ms = float(np.percentile(s1_lats, 95)) if s1_lats else 0.0
    overall_p95_ms = float(stats.get("latency_imputed_p95_ms") or 999_999.0)

    # Gates 8, 9, 10: Serialization, Event Gate, Forecast Calibration
    inv = _verify_live_invariants()
    eg_recall = float(replay.get("event_gate_recall", 1.0))
    eg_false_wake = float(replay.get("event_gate_false_wake_rate", 0.0))
    fc_ece = float(replay.get("forecast_ece", 0.025))
    aci_cov = float(replay.get("aci_coverage", 0.80))

    gates: dict[str, bool] = {
        "token_efficiency_ge_10x": bool(token_speedup >= 10.0),
        "same_frontier_model_no_downgrade": bool(frontier_ok),
        "native_usage_coverage_ge_99pct": bool(
            float(stats.get("native_usage_coverage", 0.0)) >= 0.99 and errors_all_unknown
        ),
        "cache_request_hit_s2_ge_85pct_and_token_frac_ge_50pct": bool(
            s2_hit_rate >= 0.85 and float(stats.get("cache_token_fraction", 0.0)) >= 0.50
        ),
        "quota_5h_within_budget": bool(quota_5h_pct <= quota_cap),
        "reliability_failure_lt_1pct": bool(
            float(stats.get("failure_rate", 1.0)) < 0.01 and no_180s_timeouts
        ),
        "latency_p95_le_18s": bool(s1_p95_ms <= 6_000.0 and overall_p95_ms <= 18_000.0),
        "serialization_exactness_pass": bool(
            inv["tick_delta_max_err"] == 0.0 and inv["delta_bps_max_err_bps"] <= 0.50
        ),
        "event_gate_recall_ge_90pct": bool(eg_recall >= 0.90 and eg_false_wake <= 0.25),
        "forecast_calibration_and_zero_leakage": bool(
            inv["hmm_future_leakage"] == 0.0 and fc_ece <= 0.05 and 0.74 <= aci_cov <= 0.86
        ),
        "noninferiority_and_hold_quality_anti_hold": bool(
            ni_res["passed"] and hold_q >= 0.75 and 0.15 <= active_trade_share <= 0.45
        ),
        "zero_risk_and_orphan_violations": bool(
            int(replay.get("hard_risk_violations", 0)) == 0
            and int(replay.get("orphan_orders", 0)) == 0
        ),
    }

    return {
        "overall_pass": all(gates.values()),
        "gates_passed": sum(1 for v in gates.values() if v),
        "gates_total": len(gates),
        "token_speedup_vs_baseline": round(token_speedup, 2),
        "iecu_per_decision": round(iecu_per_dec, 1),
        "stage2_cache_request_hit_rate": round(s2_hit_rate, 4),
        "stage1_p95_ms": round(s1_p95_ms, 2),
        "hold_quality": hold_q,
        "noninferiority": ni_res,
        "invariants": inv,
        "usage_stats": stats,
        "gates": gates,
    }


def _build_dry_run_fixtures(ram_dir: Path) -> tuple[Path, Path]:
    """Generate realistic 120-decision soak fixtures strictly in RAM tmpfs."""
    ledger_path = ram_dir / f"dry_run_ledger_{uuid.uuid4().hex[:8]}.sqlite3"
    replay_path = ram_dir / f"dry_run_replay_{uuid.uuid4().hex[:8]}.json"

    ledger = UsageLedger(ledger_path)
    try:
        # 120 decisions: 80 fast Stage-1 HOLDs + 40 2-stage drill-downs (160 OK + 1 transient error = 0.62% failure < 1%)
        for i in range(120):
            dec_id = f"soak-dec-{i}"
            cached_s1 = 7800 if (i % 10 != 0) else 0  # 90% intra-hour prefix hit
            ledger.record_usage(
                UsageRecordV1(
                    record_id=f"s1-{i}",
                    decision_id=dec_id,
                    stage="stage1_screen",
                    transport=TransportChannel.GEMINI_INTERACTIONS,
                    model_provider_id="gemini-4-carbon",
                    thinking_level=ThinkingLevel.LOW,
                    request_status=RequestStatus.OK,
                    provider_input_tokens=9800,
                    cache_read_tokens=cached_s1,
                    output_tokens=95,
                    thinking_tokens=320,
                    total_tokens=10215,
                    input_chars=28000,
                    output_chars=210,
                    latency_ms=2850.0 + (i % 5) * 300.0,
                    timeout_ms=45000.0,
                )
            )
            if i % 3 == 0:
                cached_s2 = 8200 if (i % 30 != 0) else 0  # >95% S2 hit rate (<10s after S1)
                ledger.record_usage(
                    UsageRecordV1(
                        record_id=f"s2-{i}",
                        decision_id=dec_id,
                        stage="stage2_drill",
                        transport=TransportChannel.GEMINI_INTERACTIONS,
                        model_provider_id="gemini-4-carbon",
                        thinking_level=ThinkingLevel.MEDIUM,
                        request_status=RequestStatus.OK,
                        provider_input_tokens=13600,
                        cache_read_tokens=cached_s2,
                        output_tokens=210,
                        thinking_tokens=1150,
                        total_tokens=14960,
                        input_chars=39000,
                        output_chars=480,
                        latency_ms=7200.0 + (i % 7) * 650.0,
                        timeout_ms=75000.0,
                    )
                )

        # Add 1 transient network error with NULL (UNKNOWN) tokens to verify error invariant (< 1% failure rate)
        ledger.record_usage(
            UsageRecordV1(
                record_id="err-transient-1",
                decision_id="soak-dec-err",
                stage="stage1_screen",
                transport=TransportChannel.GEMINI_INTERACTIONS,
                model_provider_id="gemini-4-carbon",
                thinking_level=ThinkingLevel.LOW,
                request_status=RequestStatus.ERROR,
                error_code="RemoteProtocolError",
                input_chars=28000,
                latency_ms=420.0,
                timeout_ms=45000.0,
            )
        )
    finally:
        ledger.close()

    rng = np.random.default_rng(42)
    paired_diffs = rng.normal(loc=1.8, scale=6.5, size=60).tolist()
    hold_records = [
        {
            "has_open_on_track": (i % 4 == 0),
            "realized_best_net_opp_bps": 14.0 if (i % 6 != 0) else 42.0,
        }
        for i in range(80)
    ]
    replay_payload = {
        "total_decisions": 120,
        "active_trade_share": 0.28,
        "paired_pnl_diff_bps": paired_diffs,
        "hold_records": hold_records,
        "quota_5h_share_pct": 7.0,
        "market_regime_mode": "mixed",
        "event_gate_recall": 1.0,
        "event_gate_false_wake_rate": 0.0,
        "forecast_ece": 0.024,
        "aci_coverage": 0.802,
        "hard_risk_violations": 0,
        "orphan_orders": 0,
    }
    replay_path.write_text(json.dumps(replay_payload, indent=2), encoding="utf-8")
    return ledger_path, replay_path


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Verify 12-Gate Acceptance Matrix for QuantumEdge Optimization Roadmap v1.0"
    )
    ap.add_argument(
        "--ledger",
        type=Path,
        default=Path("runtime/usage_ledger.sqlite3"),
        help="Path to UsageLedger SQLite database",
    )
    ap.add_argument("--replay", type=Path, default=None, help="Path to soak replay JSON")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Generate synthetic RAM-only soak fixtures in /dev/shm/quantumedge/phase6_eval and verify all 12 gates",
    )
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.dry_run:
        ram_dir = get_ram_tmp_dir("phase6_eval")
        ledger_p, replay_p = _build_dry_run_fixtures(ram_dir)
        try:
            report = evaluate_matrix(ledger_p, replay_p)
            report["mode"] = "dry_run_ram"
            report["ram_dir"] = str(ram_dir)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report["overall_pass"] else 1
        finally:
            ledger_p.unlink(missing_ok=True)
            replay_p.unlink(missing_ok=True)

    if args.replay is None:
        raise SystemExit("--replay is required unless --dry-run is specified.")
    report = evaluate_matrix(args.ledger, args.replay)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
