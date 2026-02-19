"""Episode replay runner for Supervisor decision core."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from policy.policy_contract import POLICY_VERSION
from supervisor.action_ledger import ActionLedger
from supervisor.guards import GuardConfig, GuardEvaluator, load_guard_config
from supervisor.regime_sm import (DirectivesConfig, RegimeConfig,
                                  RegimeStateMachine, load_directives_config,
                                  load_regime_config)
from supervisor.run_context import RunContext
from supervisor.stats import StatsAggregator

from .cutter import RollingWindow, load_scenarios
from .io import iter_ticks


@dataclass
class EpisodeRunConfig:
    episode_set: str
    manifest_path: Path
    runs_path: Path
    scenario_filter: Optional[str] = None
    replay_speed: str = "instant"
    signal_window_s: int = 5
    stats_interval_s: int = 30
    directives_interval_s: int = 10
    scenarios_path: Optional[Path] = None
    policy_path: Optional[Path] = None


def run_episode_set(cfg: EpisodeRunConfig) -> int:
    manifest = json.loads(cfg.manifest_path.read_text(encoding="utf-8"))
    scenarios_path = cfg.scenarios_path or (
        Path(__file__).resolve().parents[2] / "episodes" / "scenarios_v1.yaml"
    )
    scenarios = {s.scenario_id: s for s in load_scenarios(scenarios_path)}

    policy_path = cfg.policy_path or (
        Path(__file__).resolve().parents[2] / "config" / "policy.yaml"
    )
    regime_cfg = load_regime_config(policy_path)
    guard_cfg = load_guard_config(policy_path)
    directives_cfg = load_directives_config(policy_path)

    episodes = manifest.get("episodes", [])
    if cfg.scenario_filter:
        episodes = [
            ep for ep in episodes if ep.get("scenario_id") == cfg.scenario_filter
        ]

    if not episodes:
        print("[WARN] No episodes to run.")
        return 1

    episodes_root = manifest.get("episodes_root")
    for episode in episodes:
        _run_single_episode(
            episode,
            cfg,
            scenarios.get(episode.get("scenario_id")),
            regime_cfg,
            guard_cfg,
            directives_cfg,
            Path(episodes_root) if episodes_root else None,
        )
    return 0


def _run_single_episode(
    episode: Dict[str, object],
    cfg: EpisodeRunConfig,
    scenario,
    regime_cfg: RegimeConfig,
    guard_cfg: GuardConfig,
    directives_cfg: DirectivesConfig,
    episodes_root: Optional[Path],
) -> None:
    supervisor_root = _resolve_supervisor_root(cfg.runs_path)
    run_context = RunContext.create(
        project_root=supervisor_root,
        policy_version=POLICY_VERSION,
        model_version="none",
        episode_set=cfg.episode_set,
        episode_id=str(episode.get("episode_id")),
        scenario_id=str(episode.get("scenario_id")),
        note="offline_episode",
    )
    config_snapshot = {
        "episode": episode,
        "scenario": scenario.__dict__ if scenario else None,
        "policy": {
            "regime_sm": regime_cfg,
            "guards": guard_cfg,
            "directives": directives_cfg,
        },
        "runner": {
            "replay_speed": cfg.replay_speed,
            "signal_window_s": cfg.signal_window_s,
            "stats_interval_s": cfg.stats_interval_s,
            "directives_interval_s": cfg.directives_interval_s,
        },
    }
    run_context.write_config_snapshot(config_snapshot)
    run_context.log_event("RUN_START", {"episode": episode})

    stats = StatsAggregator(start_ts=_episode_start_ts(episode))
    action_ledger = ActionLedger(
        run_context.run_dir / "action_ledger.jsonl", run_context
    )
    regime_sm = RegimeStateMachine(regime_cfg)
    guard_eval = GuardEvaluator(guard_cfg)
    signals_window = RollingWindow(cfg.signal_window_s)
    directives_last_hash: Optional[str] = None
    wall_start = time.time()

    episode_file = _episode_path(
        supervisor_root, cfg.episode_set, episode, episodes_root
    )
    start_ts = None
    end_ts = None
    next_stats_ts = None
    next_directives_ts = None

    for tick in iter_ticks(episode_file, fmt="jsonl"):
        if start_ts is None:
            start_ts = tick.ts
            next_stats_ts = start_ts + cfg.stats_interval_s
            next_directives_ts = start_ts + cfg.directives_interval_s
        end_ts = tick.ts
        signals_window.add(tick)
        if cfg.replay_speed == "realtime" and start_ts is not None:
            _maybe_sleep(tick.ts, start_ts, wall_start)
        if next_stats_ts is not None and tick.ts >= next_stats_ts:
            guard_result = guard_eval.evaluate(_guard_context_from_tick(tick))
            run_context.log_event("GUARD_EVALUATION", guard_result.to_dict())
            if not guard_result.allowed:
                for reason in guard_result.reason_codes:
                    _record_block(run_context, stats, reason, guard_result.details)

            decision = regime_sm.evaluate(
                _signals_from_window(signals_window, tick),
                guard_result.critical,
                now_ts=tick.ts,
            )
            if decision.changed:
                stats.on_regime_change(decision.current_state, now_ts=tick.ts)
                run_context.log_event(
                    "REGIME_CHANGE",
                    {
                        "state": decision.current_state,
                        "reasons": decision.reason_codes,
                        "scores": decision.scores,
                    },
                )
            elif decision.proposed_state and decision.blocked_reason:
                action_ledger.append(
                    "ACTION_REJECTED",
                    action_type="SET_REGIME",
                    target="Supervisor",
                    payload={
                        "proposed_state": decision.proposed_state,
                        "blocked_reason": decision.blocked_reason,
                    },
                    reason_codes=decision.reason_codes,
                    status="REJECTED",
                )
                stats.on_action("REJECTED")

            snapshot = stats.snapshot(now_ts=tick.ts)
            snapshot["strategy_mode"] = decision.current_state
            snapshot["guard_allowed"] = guard_result.allowed
            run_context.log_event("STAT_SNAPSHOT", snapshot)
            next_stats_ts = tick.ts + cfg.stats_interval_s

        if next_directives_ts is not None and tick.ts >= next_directives_ts:
            guard_result = guard_eval.evaluate(_guard_context_from_tick(tick))
            decision = regime_sm.evaluate(
                _signals_from_window(signals_window, tick),
                guard_result.critical,
                now_ts=tick.ts,
            )
            directives = _build_directives(
                decision.current_state,
                guard_result,
                cfg.episode_set,
                episode,
                run_context.run_id,
            )
            changed, directives_last_hash = _update_directives(
                run_context,
                action_ledger,
                stats,
                directives,
                directives_last_hash,
                supervisor_root / "runtime",
            )
            if changed:
                run_context.log_event(
                    "DIRECTIVES_UPDATED", {"regime": decision.current_state}
                )
            next_directives_ts = tick.ts + cfg.directives_interval_s

    duration_s = int((end_ts or start_ts or time.time()) - (start_ts or time.time()))
    run_context.log_event(
        "RUN_END", {"duration_s": duration_s, "stop_reason": "episode_complete"}
    )
    summary = _build_summary(start_ts, end_ts)
    summary.update(stats.finalize(now_ts=end_ts or time.time()))
    run_context.write_summary(summary)
    run_context.write_artifacts_manifest()


def _episode_start_ts(episode: Dict[str, object]) -> float:
    t0 = episode.get("t0")
    try:
        return float(t0)
    except (TypeError, ValueError):
        return time.time()


def _episode_path(
    supervisor_root: Path,
    episode_set: str,
    episode: Dict[str, object],
    episodes_root: Optional[Path],
) -> Path:
    rel_path = episode.get("episode_path")
    if episodes_root and rel_path:
        return episodes_root / str(rel_path)
    scenario_id = str(episode.get("scenario_id"))
    episode_id = str(episode.get("episode_id"))
    return (
        supervisor_root
        / "runtime"
        / "episodes"
        / episode_set
        / scenario_id
        / f"{episode_id}.jsonl"
    )


def _guard_context_from_tick(tick) -> Dict[str, Optional[float]]:
    spread_bps = None
    if tick.bid is not None and tick.ask is not None:
        mid = (tick.bid + tick.ask) / 2.0
        if mid:
            spread_bps = (tick.ask - tick.bid) / mid * 10000.0
    return {
        "spread_bps": spread_bps,
        "depth_usd": None,
        "margin_used_pct": None,
        "liq_distance_pct": None,
        "drawdown_pct": None,
        "loss_streak": None,
        "trades_per_hour": None,
    }


def _signals_from_window(window: RollingWindow, tick) -> Dict[str, Optional[float]]:
    stats = window.stats()
    return {
        "trend_score": stats.get("return_bps"),
        "volatility": stats.get("volatility_bps"),
        "spread_bps": _guard_context_from_tick(tick).get("spread_bps"),
    }


def _build_directives(
    regime_state: str,
    guard_result,
    episode_set: str,
    episode: Dict[str, object],
    run_id: str,
) -> Dict[str, object]:
    allow_scalp = guard_result.allowed and regime_state in {"RANGE", "TREND"}
    return {
        "ts_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "regime": regime_state,
        "allow": {
            "scalp_enter": bool(allow_scalp),
            "scalp_increase": bool(allow_scalp),
            "lock_freeze": regime_state in {"PANIC", "FREEZE"},
            "lock_unwind": regime_state == "UNWIND",
        },
        "blocked_reasons": list(guard_result.reason_codes),
        "guard_summary": guard_result.to_dict(),
        "episode_tags": {
            "episode_set": episode_set,
            "scenario_id": episode.get("scenario_id"),
            "episode_id": episode.get("episode_id"),
        },
    }


def _update_directives(
    run_context: RunContext,
    action_ledger: ActionLedger,
    stats: StatsAggregator,
    directives: Dict[str, object],
    last_hash: Optional[str],
    runtime_root: Path,
) -> tuple[bool, str]:
    payload_json = json.dumps(directives, sort_keys=True, default=str)
    new_hash = _sha256(payload_json)
    if last_hash and new_hash == last_hash:
        return False, last_hash
    action_id = action_ledger.append(
        "ACTION_PROPOSED",
        action_type="UPDATE_DIRECTIVES",
        target="AllBots",
        payload=directives,
        reason_codes=directives.get("blocked_reasons", []),
        status="PROPOSED",
    )
    stats.on_action("PROPOSED")
    action_ledger.append(
        "ACTION_APPLIED",
        action_type="UPDATE_DIRECTIVES",
        target="AllBots",
        payload=directives,
        reason_codes=directives.get("blocked_reasons", []),
        action_id=action_id,
        status="APPLIED",
    )
    stats.on_action("APPLIED")
    run_context.update_directives(directives, runtime_root)
    return True, new_hash


def _record_block(
    run_context: RunContext,
    stats: StatsAggregator,
    reason: str,
    details: Dict[str, object],
) -> None:
    stats.on_block(reason, details)
    payload = {"reason_code": reason}
    if details:
        payload.update({"details": details})
    run_context.log_event("BLOCK_REASON", payload)


def _build_summary(
    start_ts: Optional[float], end_ts: Optional[float]
) -> Dict[str, object]:
    start = start_ts or time.time()
    end = end_ts or time.time()
    return {
        "start_ts_utc": datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
        "end_ts_utc": datetime.fromtimestamp(end, tz=timezone.utc).isoformat(),
        "duration_s": int(end - start),
        "pnl_total": None,
        "wins": None,
        "losses": None,
        "trades": None,
        "max_drawdown": None,
        "max_margin_used": None,
        "min_liq_distance": None,
        "fees_paid": None,
        "funding_paid": None,
        "regime_time_share": {},
        "blocked_actions_count": 0,
        "errors_count": 0,
    }


def _sha256(payload: str) -> str:
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _maybe_sleep(current_ts: float, start_ts: float, wall_start: float) -> None:
    # Simple real-time pacing for debugging (best-effort).
    elapsed = current_ts - start_ts
    target = wall_start + elapsed
    now = time.time()
    if target > now:
        time.sleep(min(target - now, 0.5))


def _resolve_supervisor_root(runs_path: Path) -> Path:
    if runs_path.name == "runs" and runs_path.parent.name == "runtime":
        return runs_path.parent.parent
    return Path(__file__).resolve().parents[2]
