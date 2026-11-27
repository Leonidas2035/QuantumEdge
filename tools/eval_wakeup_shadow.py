#!/usr/bin/env python3
"""72-Hour Shadow Replay Evaluator for EventDrivenWakeupGate (RAM-First).

Simulates or replays 72 hours across Quiet, Mixed, and Volatile market regimes
for BTCUSDT (0.25 bps spread) and SOLUSDT (26.0 bps spread), verifying:
  - Material-Event Recall >= 90%
  - False Wake Rate <= 25%
  - Missed Opportunity Rate <= 10%
  - Projected daily wakeup rates vs 144/day 10m polling baseline
Writes all shadow logs and summary JSON strictly to /dev/shm/quantumedge/phase5_shadow.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from quantum_edge_core.contracts.optimization import DecisionEventV1  # noqa: E402
from quantum_edge_core.execution.storage_policy import get_ram_tmp_dir  # noqa: E402
from quantum_edge_core.market_data.wakeup_gate import (  # noqa: E402
    EventDrivenWakeupGate,
    ShadowWakeupLogger,
)


@dataclass(slots=True)
class ScheduledMaterialEvent:
    event_idx: int
    symbol: str
    start_step: int
    end_step: int
    move_bps: float
    net_rr: float
    kind: str  # IMPULSE_60BPS | LIQ_SWEEP


def _generate_72h_stream(
    *,
    hours: int = 72,
    seed: int = 42,
    step_sec: int = 15,
) -> tuple[list[dict[str, Any]], list[ScheduledMaterialEvent]]:
    """Synthesize a deterministic 72h 2-asset (BTCUSDT + SOLUSDT) stream with ground-truth events.

    Regimes across ``hours``:
      - First third: Quiet Range (low noise, 4 material events / 24h)
      - Middle third: Mixed 70/30 (moderate activity, 10 material events / 24h)
      - Final third: Volatile Cascade (active breakouts, 22 material events / 24h)
    """
    rng = np.random.default_rng(seed)
    total_steps = int((hours * 3600) // step_sec)
    t_base = 1_795_644_000.0  # Aligned on exact hour boundary

    btc_p = 60_000.0
    sol_p = 145.0

    # Place deterministic material events spaced >= 45m apart
    material_events: list[ScheduledMaterialEvent] = []
    q_end = total_steps // 3
    m_end = (2 * total_steps) // 3

    def _schedule_zone(start_s: int, end_s: int, count: int, base_idx: int) -> None:
        if count <= 0 or end_s <= start_s + 100:
            return
        spacing = (end_s - start_s - 80) // max(count, 1)
        for i in range(count):
            st_step = start_s + 40 + i * spacing
            sym = "BTCUSDT" if (i % 3 != 2) else "SOLUSDT"
            move_bps = 85.0 if sym == "SOLUSDT" else 68.0
            kind = "IMPULSE_60BPS" if (i % 2 == 0) else "LIQ_SWEEP"
            material_events.append(
                ScheduledMaterialEvent(
                    event_idx=base_idx + i,
                    symbol=sym,
                    start_step=st_step,
                    end_step=st_step + 16,  # 4 minutes impulse
                    move_bps=move_bps * (1.0 if i % 2 == 0 else -1.0),
                    net_rr=2.1,
                    kind=kind,
                )
            )

    scale = max(1.0, hours / 72.0)
    _schedule_zone(0, q_end, max(2, int(round(4 * scale))), 0)
    _schedule_zone(q_end, m_end, max(4, int(round(10 * scale))), 100)
    _schedule_zone(m_end, total_steps, max(8, int(round(22 * scale))), 200)

    active_by_step: dict[int, ScheduledMaterialEvent] = {}
    for ev in material_events:
        for s in range(ev.start_step, ev.end_step + 1):
            active_by_step[s] = ev

    ticks: list[dict[str, Any]] = []
    for step in range(total_steps):
        ts_sec = t_base + step * step_sec
        ev = active_by_step.get(step)

        # Micro-noise (~1.2 bps std per 15s -> below 3*5bps=15bps CUSUM floor unless drifting/impulse)
        btc_ret = float(rng.normal(0.0, 0.00008))
        sol_ret = float(rng.normal(0.0, 0.00010))
        btc_cvd_z = float(rng.normal(0.0, 0.45))
        sol_cvd_z = float(rng.normal(0.0, 0.45))
        btc_liq_dist: float | None = None
        btc_liq_usd = 0.0
        sol_liq_dist: float | None = 28.0  # Persistent 28 bps alt noise (< 2.5 * 26 = 65 bps -> MUST NOT wake!)
        sol_liq_usd = 650_000.0

        if ev is not None:
            per_step_ret = (ev.move_bps / 10_000.0) / float(ev.end_step - ev.start_step + 1)
            sign = 1.0 if ev.move_bps > 0 else -1.0
            if ev.symbol == "BTCUSDT":
                btc_ret = per_step_ret
                if step == ev.start_step + 2:
                    btc_cvd_z = 2.9 * sign
                    if ev.kind == "LIQ_SWEEP":
                        btc_liq_dist = 24.0
                        btc_liq_usd = 900_000.0
            else:
                sol_ret = per_step_ret
                if step == ev.start_step + 2:
                    sol_cvd_z = 2.9 * sign
                    if ev.kind == "LIQ_SWEEP":
                        sol_liq_dist = 70.0  # >= 2.5 * 26 = 65 bps AND <= 1.2 * 65 = 78 bps!
                        sol_liq_usd = 750_000.0

        btc_p *= math.exp(btc_ret)
        sol_p *= math.exp(sol_ret)

        ticks.append(
            {
                "step": step,
                "ts_sec": ts_sec,
                "btc_price": btc_p,
                "sol_price": sol_p,
                "btc_cvd_z": btc_cvd_z,
                "sol_cvd_z": sol_cvd_z,
                "btc_liq_dist": btc_liq_dist,
                "btc_liq_usd": btc_liq_usd,
                "sol_liq_dist": sol_liq_dist,
                "sol_liq_usd": sol_liq_usd,
            }
        )

    return ticks, material_events


async def run_shadow_evaluation(
    *,
    hours: int = 72,
    seed: int = 42,
    out_json: Path | None = None,
) -> dict[str, Any]:
    ram_dir = get_ram_tmp_dir("phase5_shadow")
    shadow_log_path = ram_dir / "wakeup_shadow_log.jsonl"
    shadow_log_path.unlink(missing_ok=True)

    ticks, material_events = _generate_72h_stream(hours=hours, seed=seed, step_sec=15)
    emitted: list[tuple[int, DecisionEventV1]] = []
    cur_step = {"idx": 0}

    async def _noop_live(ev: DecisionEventV1) -> None:
        raise AssertionError(f"shadow_mode=True must not wake live LLM: {ev.event_id}")

    async def _on_shadow(ev: DecisionEventV1) -> None:
        emitted.append((cur_step["idx"], ev))

    logger = ShadowWakeupLogger(shadow_log_path, flush_max_items=500, flush_interval_sec=300.0)
    gate = EventDrivenWakeupGate(
        _noop_live,
        shadow_mode=True,
        coalesce_ms=700,
        per_symbol_cooldown_sec=90.0,
        virtual_clock=True,
        shadow_logger=logger,
        on_shadow_event=_on_shadow,
    )

    for row in ticks:
        cur_step["idx"] = int(row["step"])
        ts_sec = float(row["ts_sec"])
        await gate.ingest_tick(
            symbol="BTCUSDT",
            price=float(row["btc_price"]),
            ts_sec=ts_sec,
            is_trade=False,
            spread_bps=0.25,
            atr_15m_bps=35.0,
            nearest_liq_dist_bps=row["btc_liq_dist"],
            nearest_liq_usd=float(row["btc_liq_usd"]),
            cvd_zscore_5m=float(row["btc_cvd_z"]),
        )
        await gate.ingest_tick(
            symbol="SOLUSDT",
            price=float(row["sol_price"]),
            ts_sec=ts_sec + 0.05,
            is_trade=False,
            spread_bps=26.0,
            atr_15m_bps=65.0,
            nearest_liq_dist_bps=row["sol_liq_dist"],
            nearest_liq_usd=float(row["sol_liq_usd"]),
            cvd_zscore_5m=float(row["sol_cvd_z"]),
        )

    await gate.flush_pending()
    logger.close()

    # Evaluate Recall & Missed Opportunity Rate
    detected_count = 0
    missed_high_rr = 0
    for mev in material_events:
        hit = any(
            (mev.start_step <= w_step <= mev.end_step + 4) and (mev.symbol in ev.symbols)
            for w_step, ev in emitted
        )
        if hit:
            detected_count += 1
        elif abs(mev.move_bps) >= 60.0 and mev.net_rr >= 1.5:
            missed_high_rr += 1

    recall = detected_count / max(len(material_events), 1)
    missed_opp_rate = missed_high_rr / max(len(material_events), 1)

    # Evaluate False Wake Rate on signal-driven wakeups (excluding scheduled hourly heartbeats)
    signal_wakes = [
        (w_step, ev)
        for w_step, ev in emitted
        if not all("HEARTBEAT_1H" in r for r in ev.reasons)
    ]
    false_wakes = 0
    horizon_steps = 60  # 15 minutes at 15s step
    btc_prices = np.array([float(t["btc_price"]) for t in ticks], dtype=np.float64)
    sol_prices = np.array([float(t["sol_price"]) for t in ticks], dtype=np.float64)

    for w_step, ev in signal_wakes:
        end_idx = min(len(ticks), w_step + horizon_steps)
        cleared_cost = False
        for sym in ev.symbols:
            arr = btc_prices if sym == "BTCUSDT" else sol_prices
            cost_bps = 5.25 if sym == "BTCUSDT" else 31.0  # 1.0x (spread + fees)
            seg = arr[w_step : max(w_step + 2, end_idx)]
            rng_bps = (float(np.max(seg)) - float(np.min(seg))) / float(seg[0]) * 10_000.0
            if rng_bps >= cost_bps:
                cleared_cost = True
                break
        if not cleared_cost:
            false_wakes += 1

    false_wake_rate = false_wakes / max(len(signal_wakes), 1)

    # Daily wakeup rate breakdown across the 3 regime thirds
    total_steps = len(ticks)
    q_end = total_steps // 3
    m_end = (2 * total_steps) // 3
    days_per_zone = max((hours / 3.0) / 24.0, 1e-6)

    q_wakes = sum(1 for s, _ in emitted if s < q_end) / days_per_zone
    m_wakes = sum(1 for s, _ in emitted if q_end <= s < m_end) / days_per_zone
    v_wakes = sum(1 for s, _ in emitted if s >= m_end) / days_per_zone

    summary = {
        "hours": hours,
        "seed": seed,
        "total_material_events": len(material_events),
        "detected_material_events": detected_count,
        "material_event_recall": round(recall, 4),
        "signal_wake_events": len(signal_wakes),
        "total_shadow_events": len(emitted),
        "false_wake_rate": round(false_wake_rate, 4),
        "missed_opportunity_rate": round(missed_opp_rate, 4),
        "quiet_wakes_per_day": round(q_wakes, 2),
        "mixed_wakes_per_day": round(m_wakes, 2),
        "volatile_wakes_per_day": round(v_wakes, 2),
        "baseline_polling_wakes_per_day": 144.0,
        "ram_shadow_log": str(shadow_log_path),
    }

    target_json = out_json or (ram_dir / "eval_summary.json")
    target_json.parent.mkdir(parents=True, exist_ok=True)
    target_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["out_json"] = str(target_json)
    return summary


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="72h Shadow Replay Evaluator for EventDrivenWakeupGate")
    ap.add_argument("--hours", type=int, default=72)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-ram", type=Path, default=None)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = asyncio.run(
        run_shadow_evaluation(hours=args.hours, seed=args.seed, out_json=args.out_ram)
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
