"""Offline episode evaluation report generator."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable


def generate_report(
    episode_set: str,
    runs_path: Path,
    out_dir: Path,
) -> Path:
    runs = _find_episode_runs(episode_set, runs_path)
    if not runs:
        raise FileNotFoundError(
            f"No runs found for episode_set={episode_set} under {runs_path}"
        )

    report = _aggregate_runs(episode_set, runs)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _write_report_md(out_dir / "report.md", report)
    return report_path


def _find_episode_runs(episode_set: str, runs_path: Path) -> list[Path]:
    runs: list[Path] = []
    if not runs_path.exists():
        return runs
    for run_dir in runs_path.iterdir():
        if not run_dir.is_dir():
            continue
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if summary.get("episode_set") == episode_set:
            runs.append(run_dir)
    return runs


def _aggregate_runs(episode_set: str, run_dirs: Iterable[Path]) -> Dict[str, object]:
    totals: Dict[str, float] = defaultdict(float)
    block_reasons: Dict[str, int] = defaultdict(int)
    scenario_stats: Dict[str, Dict[str, object]] = {}
    total_flaps = 0
    total_duration = 0.0
    total_runs = 0

    for run_dir in run_dirs:
        summary_path = run_dir / "summary.json"
        events_path = run_dir / "events.jsonl"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        scenario_id = summary.get("scenario_id") or "unknown"
        duration = float(summary.get("duration_s") or 0.0)
        total_duration += duration
        total_runs += 1
        totals["blocked_actions_count"] += float(
            summary.get("blocked_actions_count") or 0.0
        )
        totals["actions_proposed_count"] += float(
            summary.get("actions_proposed_count") or 0.0
        )
        totals["actions_applied_count"] += float(
            summary.get("actions_applied_count") or 0.0
        )
        totals["actions_rejected_count"] += float(
            summary.get("actions_rejected_count") or 0.0
        )
        totals["errors_count"] += float(summary.get("errors_count") or 0.0)

        regime_share = summary.get("regime_time_share") or {}
        for regime, seconds in regime_share.items():
            totals[f"regime_{regime}"] += float(seconds or 0.0)

        block_reason_map = summary.get("block_reasons") or {}
        for reason, count in block_reason_map.items():
            block_reasons[str(reason)] += int(count or 0)

        flaps = _count_regime_flaps(events_path)
        total_flaps += flaps

        scenario_bucket = scenario_stats.setdefault(
            scenario_id,
            {
                "runs": 0,
                "duration_s": 0.0,
                "blocked_actions_count": 0,
                "actions_proposed_count": 0,
                "actions_applied_count": 0,
                "actions_rejected_count": 0,
                "errors_count": 0,
                "regime_flaps": 0,
                "block_reasons": defaultdict(int),
            },
        )
        scenario_bucket["runs"] += 1
        scenario_bucket["duration_s"] += duration
        scenario_bucket["blocked_actions_count"] += int(
            summary.get("blocked_actions_count") or 0
        )
        scenario_bucket["actions_proposed_count"] += int(
            summary.get("actions_proposed_count") or 0
        )
        scenario_bucket["actions_applied_count"] += int(
            summary.get("actions_applied_count") or 0
        )
        scenario_bucket["actions_rejected_count"] += int(
            summary.get("actions_rejected_count") or 0
        )
        scenario_bucket["errors_count"] += int(summary.get("errors_count") or 0)
        scenario_bucket["regime_flaps"] += flaps
        for reason, count in block_reason_map.items():
            scenario_bucket["block_reasons"][str(reason)] += int(count or 0)

    report = {
        "episode_set": episode_set,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "runs": total_runs,
        "total_duration_s": total_duration,
        "regime_time_share_s": {
            key.replace("regime_", ""): value
            for key, value in totals.items()
            if key.startswith("regime_")
        },
        "blocked_actions_count": int(totals["blocked_actions_count"]),
        "actions_proposed_count": int(totals["actions_proposed_count"]),
        "actions_applied_count": int(totals["actions_applied_count"]),
        "actions_rejected_count": int(totals["actions_rejected_count"]),
        "errors_count": int(totals["errors_count"]),
        "regime_flaps": total_flaps,
        "regime_flaps_per_min": _rate_per_minute(total_flaps, total_duration),
        "top_block_reasons": _top_reasons(block_reasons),
        "scenarios": _render_scenarios(scenario_stats),
    }
    return report


def _count_regime_flaps(events_path: Path) -> int:
    if not events_path.exists():
        return 0
    flaps = 0
    with events_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "REGIME_CHANGE":
                flaps += 1
    return flaps


def _rate_per_minute(count: int, duration_s: float) -> float:
    if duration_s <= 0:
        return 0.0
    return float(count) / max(duration_s / 60.0, 1e-6)


def _top_reasons(reasons: Dict[str, int], limit: int = 5) -> Dict[str, int]:
    return dict(sorted(reasons.items(), key=lambda item: item[1], reverse=True)[:limit])


def _render_scenarios(stats: Dict[str, Dict[str, object]]) -> list[Dict[str, object]]:
    rendered = []
    for scenario_id, data in sorted(stats.items(), key=lambda item: item[0]):
        duration = float(data.get("duration_s") or 0.0)
        flaps = int(data.get("regime_flaps") or 0)
        block_reasons = data.get("block_reasons") or {}
        rendered.append(
            {
                "scenario_id": scenario_id,
                "runs": data.get("runs", 0),
                "duration_s": duration,
                "blocked_actions_count": data.get("blocked_actions_count", 0),
                "actions_proposed_count": data.get("actions_proposed_count", 0),
                "actions_applied_count": data.get("actions_applied_count", 0),
                "actions_rejected_count": data.get("actions_rejected_count", 0),
                "errors_count": data.get("errors_count", 0),
                "regime_flaps": flaps,
                "regime_flaps_per_min": _rate_per_minute(flaps, duration),
                "top_block_reasons": _top_reasons(block_reasons),
            }
        )
    return rendered


def _write_report_md(path: Path, report: Dict[str, object]) -> None:
    lines = [
        f"# Episode Report: {report.get('episode_set')}",
        "",
        f"Generated at: {report.get('generated_at')}",
        "",
        "## Summary",
        f"- Runs: {report.get('runs')}",
        f"- Total duration (s): {report.get('total_duration_s')}",
        f"- Regime flaps: {report.get('regime_flaps')} (per min: {report.get('regime_flaps_per_min'):.2f})",
        f"- Blocked actions: {report.get('blocked_actions_count')}",
        f"- Actions proposed/applied/rejected: {report.get('actions_proposed_count')}/"
        f"{report.get('actions_applied_count')}/{report.get('actions_rejected_count')}",
        f"- Errors: {report.get('errors_count')}",
        "",
        "## Top Block Reasons",
    ]
    for reason, count in (report.get("top_block_reasons") or {}).items():
        lines.append(f"- {reason}: {count}")
    lines.append("")
    lines.append("## Scenario Breakdown")
    for scenario in report.get("scenarios", []):
        lines.append(
            f"- {scenario.get('scenario_id')}: runs={scenario.get('runs')}, "
            f"blocked={scenario.get('blocked_actions_count')}, "
            f"flaps/min={scenario.get('regime_flaps_per_min'):.2f}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")
