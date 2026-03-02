"""Regression gate runner for Ops Brain v1."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, Optional

from quantum_edge_core.supervisor.supervisor.episodes.runner import EpisodeRunConfig, run_episode_set
from quantum_edge_core.supervisor.supervisor.episodes.report import generate_report
from quantum_edge_core.supervisor.supervisor.episodes.cutter import load_scenarios
from quantum_edge_core.supervisor.supervisor.ops.config import load_ops_config, get_nested

CRITICAL_REASONS = {
    "MAX_MARGIN_USED_PCT",
    "MIN_LIQ_DISTANCE_PCT",
    "MAX_DRAWDOWN_PCT",
    "DAILY_LOSS_LIMIT",
}


def run_regression_gates(
    episode_set: str,
    runtime_dir: Path,
    candidate_policy_path: Path,
    baseline_policy_path: Path,
    gate_suite: str = "smoke",
    scenarios_path: Optional[Path] = None,
) -> Dict[str, Any]:
    config_dir = runtime_dir.parent / "SupervisorAgent" / "config"
    ops_cfg = load_ops_config(config_dir)
    gate_cfg = get_nested(ops_cfg, "regression_gates", {})
    runs_dir = runtime_dir / "runs"

    manifest_path = runtime_dir / "episodes" / episode_set / "episodes_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Episode manifest not found: {manifest_path}")

    baseline_report = _load_baseline_report(runtime_dir, episode_set)
    if baseline_report is None:
        baseline_report = _run_and_report(
            episode_set,
            runs_dir,
            manifest_path,
            baseline_policy_path,
            runtime_dir / "regression" / "baseline",
            gate_suite="core",
            scenarios_path=scenarios_path,
            gate_cfg=gate_cfg,
        )

    candidate_report = _run_and_report(
        episode_set,
        runs_dir,
        manifest_path,
        candidate_policy_path,
        runtime_dir / "regression" / "candidate",
        gate_suite=gate_suite,
        scenarios_path=scenarios_path,
        gate_cfg=gate_cfg,
    )

    checks = _compare_reports(candidate_report, baseline_report, gate_cfg)
    passed = all(check["passed"] for check in checks)
    return {
        "passed": passed,
        "gate_suite": gate_suite,
        "baseline_report": baseline_report,
        "candidate_report": candidate_report,
        "checks": checks,
    }


def _load_baseline_report(
    runtime_dir: Path, episode_set: str
) -> Optional[Dict[str, Any]]:
    report_path = runtime_dir / "reports" / episode_set / "report.json"
    if not report_path.exists():
        return None
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _run_and_report(
    episode_set: str,
    runs_dir: Path,
    manifest_path: Path,
    policy_path: Path,
    out_dir: Path,
    gate_suite: str,
    scenarios_path: Optional[Path],
    gate_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    effective_manifest = _prepare_manifest(
        manifest_path, gate_suite, scenarios_path, gate_cfg
    )
    cfg = EpisodeRunConfig(
        episode_set=episode_set,
        manifest_path=effective_manifest,
        runs_path=runs_dir,
        replay_speed="instant",
        policy_path=policy_path,
        scenarios_path=scenarios_path,
    )
    run_episode_set(cfg)
    report_path = generate_report(episode_set, runs_dir, out_dir)
    return json.loads(report_path.read_text(encoding="utf-8"))


def _prepare_manifest(
    manifest_path: Path,
    gate_suite: str,
    scenarios_path: Optional[Path],
    gate_cfg: Dict[str, Any],
) -> Path:
    if gate_suite == "core":
        return manifest_path
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    episodes = payload.get("episodes", [])
    if gate_suite == "panic":
        if scenarios_path is None:
            scenarios_path = (
                Path(__file__).resolve().parents[2] / "episodes" / "scenarios_v1.yaml"
            )
        panic_ids = {
            s.scenario_id
            for s in load_scenarios(scenarios_path)
            if "PANIC" in s.tags or "FREEZE" in s.tags
        }
        episodes = [ep for ep in episodes if ep.get("scenario_id") in panic_ids]
    elif gate_suite == "smoke":
        max_episodes = int(gate_cfg.get("smoke_max_episodes", 5))
        seed = int(gate_cfg.get("smoke_seed", 42))
        rng = random.Random(seed)
        if len(episodes) > max_episodes:
            episodes = rng.sample(episodes, max_episodes)

    tmp_path = manifest_path.parent / f"episodes_manifest_{gate_suite}.json"
    payload["episodes"] = episodes
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return tmp_path


def _compare_reports(
    candidate: Dict[str, Any], baseline: Dict[str, Any], gate_cfg: Dict[str, Any]
) -> list[Dict[str, Any]]:
    checks: list[Dict[str, Any]] = []
    max_flaps_abs = float(gate_cfg.get("max_regime_flaps_per_min", 2.0))
    flap_increase_pct = float(gate_cfg.get("max_flap_increase_pct", 0.2))
    blocked_increase_pct = float(gate_cfg.get("max_blocked_increase_pct", 0.3))
    rejected_increase_pct = float(
        gate_cfg.get("max_actions_rejected_increase_pct", 0.3)
    )
    errors_increase = int(gate_cfg.get("max_errors_increase", 1))
    critical_increase_pct = float(gate_cfg.get("max_critical_blocks_increase_pct", 0.2))

    candidate_flaps = float(candidate.get("regime_flaps_per_min") or 0.0)
    baseline_flaps = float(baseline.get("regime_flaps_per_min") or 0.0)
    flaps_limit = max(max_flaps_abs, baseline_flaps * (1 + flap_increase_pct))
    checks.append(
        _check(
            "regime_flaps_per_min",
            candidate_flaps,
            flaps_limit,
            candidate_flaps <= flaps_limit,
        )
    )

    candidate_blocked = int(candidate.get("blocked_actions_count") or 0)
    baseline_blocked = int(baseline.get("blocked_actions_count") or 0)
    blocked_limit = int(round(baseline_blocked * (1 + blocked_increase_pct))) + 1
    checks.append(
        _check(
            "blocked_actions_count",
            candidate_blocked,
            blocked_limit,
            candidate_blocked <= blocked_limit,
        )
    )

    candidate_rejected = int(candidate.get("actions_rejected_count") or 0)
    baseline_rejected = int(baseline.get("actions_rejected_count") or 0)
    rejected_limit = int(round(baseline_rejected * (1 + rejected_increase_pct))) + 1
    checks.append(
        _check(
            "actions_rejected_count",
            candidate_rejected,
            rejected_limit,
            candidate_rejected <= rejected_limit,
        )
    )

    candidate_errors = int(candidate.get("errors_count") or 0)
    baseline_errors = int(baseline.get("errors_count") or 0)
    error_limit = baseline_errors + errors_increase
    checks.append(
        _check(
            "errors_count",
            candidate_errors,
            error_limit,
            candidate_errors <= error_limit,
        )
    )

    candidate_critical = _count_critical(candidate.get("top_block_reasons") or {})
    baseline_critical = _count_critical(baseline.get("top_block_reasons") or {})
    critical_limit = int(round(baseline_critical * (1 + critical_increase_pct))) + 1
    checks.append(
        _check(
            "critical_block_reasons",
            candidate_critical,
            critical_limit,
            candidate_critical <= critical_limit,
        )
    )
    return checks


def _check(name: str, actual: float, limit: float, passed: bool) -> Dict[str, Any]:
    return {"name": name, "actual": actual, "limit": limit, "passed": passed}


def _count_critical(reasons: Dict[str, Any]) -> int:
    count = 0
    for reason, value in reasons.items():
        if str(reason) in CRITICAL_REASONS:
            try:
                count += int(value)
            except (TypeError, ValueError):
                continue
    return count
