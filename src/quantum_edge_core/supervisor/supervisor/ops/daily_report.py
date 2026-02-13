"""Daily operational report generator for Ops Brain v1."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def generate_daily_report(
    target_date: date,
    runtime_dir: Path,
    output_dir: Path,
    telemetry_path: Optional[Path] = None,
) -> Path:
    runs_dir = runtime_dir / "runs"
    summaries = _load_summaries_for_date(runs_dir, target_date)
    totals = _aggregate_summaries(summaries)
    ml_stats = _aggregate_ml_stats(telemetry_path, target_date)
    policy_changes = _collect_policy_changes(runtime_dir, target_date)

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{target_date.isoformat()}_report.md"
    report_path.write_text(_render_markdown(target_date, totals, ml_stats, policy_changes), encoding="utf-8")
    return report_path


def _load_summaries_for_date(runs_dir: Path, target_date: date) -> list[Dict[str, Any]]:
    summaries: list[Dict[str, Any]] = []
    if not runs_dir.exists():
        return summaries
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            continue
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        ts = payload.get("start_ts_utc") or payload.get("end_ts_utc")
        if not ts:
            continue
        try:
            ts_dt = _parse_iso(ts).date()
        except Exception:
            continue
        if ts_dt == target_date:
            summaries.append(payload)
    return summaries


def _aggregate_summaries(summaries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    totals: Dict[str, Any] = defaultdict(float)
    block_reasons: Dict[str, int] = defaultdict(int)
    regime_share: Dict[str, float] = defaultdict(float)
    errors = 0
    runs = 0
    for summary in summaries:
        runs += 1
        totals["duration_s"] += float(summary.get("duration_s") or 0.0)
        totals["trades"] += float(summary.get("trades") or 0.0)
        totals["wins"] += float(summary.get("wins") or 0.0)
        totals["losses"] += float(summary.get("losses") or 0.0)
        totals["blocked_actions_count"] += float(summary.get("blocked_actions_count") or 0.0)
        totals["actions_proposed_count"] += float(summary.get("actions_proposed_count") or 0.0)
        totals["actions_applied_count"] += float(summary.get("actions_applied_count") or 0.0)
        totals["actions_rejected_count"] += float(summary.get("actions_rejected_count") or 0.0)
        totals["errors_count"] += float(summary.get("errors_count") or 0.0)
        pnl = summary.get("pnl_total")
        if pnl is not None:
            totals["pnl_total"] += float(pnl)

        for regime, seconds in (summary.get("regime_time_share") or {}).items():
            regime_share[str(regime)] += float(seconds or 0.0)
        for reason, count in (summary.get("block_reasons") or {}).items():
            block_reasons[str(reason)] += int(count or 0)
        errors += int(summary.get("errors_count") or 0)

    winrate = None
    wins = totals.get("wins", 0.0)
    losses = totals.get("losses", 0.0)
    if wins + losses > 0:
        winrate = wins / max(wins + losses, 1)

    return {
        "runs": runs,
        "duration_s": totals.get("duration_s", 0.0),
        "trades": int(totals.get("trades", 0.0)),
        "wins": int(wins),
        "losses": int(losses),
        "winrate": winrate,
        "pnl_total": totals.get("pnl_total"),
        "blocked_actions_count": int(totals.get("blocked_actions_count", 0.0)),
        "actions_proposed_count": int(totals.get("actions_proposed_count", 0.0)),
        "actions_applied_count": int(totals.get("actions_applied_count", 0.0)),
        "actions_rejected_count": int(totals.get("actions_rejected_count", 0.0)),
        "errors_count": int(errors),
        "regime_time_share": dict(regime_share),
        "top_block_reasons": _top_reasons(block_reasons),
    }


def _aggregate_ml_stats(telemetry_path: Optional[Path], target_date: date) -> Dict[str, Any]:
    if telemetry_path is None or not telemetry_path.exists():
        return {}

    tail = deque(maxlen=5000)
    try:
        with telemetry_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                tail.append(line)
    except OSError:
        return {}

    snapshot_count = 0
    blocked_sum = 0
    drift_high = 0
    model_versions: Dict[str, Any] = {}
    for line in tail:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(payload.get("type")) != "ml_snapshot":
            continue
        ts = payload.get("ts")
        if ts is None:
            continue
        ts_dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).date()
        if ts_dt != target_date:
            continue
        data = payload.get("data") or {}
        snapshot_count += 1
        try:
            blocked_sum += int(data.get("blocked_entries") or 0)
        except (TypeError, ValueError):
            pass
        drift = data.get("drift") or {}
        try:
            score = float(drift.get("score"))
        except (TypeError, ValueError):
            score = None
        if score is not None and score >= 3.0:
            drift_high += 1
        versions = data.get("model_versions") or {}
        if isinstance(versions, dict):
            model_versions = versions
    return {
        "snapshot_count": snapshot_count,
        "blocked_avg": (blocked_sum / snapshot_count) if snapshot_count else None,
        "drift_warnings": drift_high,
        "model_versions": model_versions,
    }


def _collect_policy_changes(runtime_dir: Path, target_date: date) -> list[Dict[str, Any]]:
    versions_dir = runtime_dir / "policy_versions"
    changes: list[Dict[str, Any]] = []
    if not versions_dir.exists():
        return changes
    for manifest_path in versions_dir.glob("policy_v*_manifest.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        created = payload.get("created_at")
        if not created:
            continue
        try:
            created_dt = _parse_iso(created).date()
        except Exception:
            continue
        if created_dt == target_date:
            changes.append(payload)
    return sorted(changes, key=lambda item: item.get("created_at") or "")


def _render_markdown(
    target_date: date, totals: Dict[str, Any], ml_stats: Dict[str, Any], policy_changes: list[Dict[str, Any]]
) -> str:
    lines = [
        f"# Supervisor Daily Report — {target_date.isoformat()}",
        "",
        "## Runs",
        f"- Runs: {totals.get('runs', 0)}",
        f"- Duration (s): {totals.get('duration_s', 0):.0f}",
        f"- Errors: {totals.get('errors_count', 0)}",
        "",
        "## Performance",
        f"- Trades: {totals.get('trades', 0)}",
        f"- Wins/Losses: {totals.get('wins', 0)}/{totals.get('losses', 0)}",
    ]
    if totals.get("winrate") is not None:
        lines.append(f"- Winrate: {totals.get('winrate'):.2%}")
    if totals.get("pnl_total") is not None:
        lines.append(f"- PnL total: {totals.get('pnl_total'):.2f}")

    lines.extend(
        [
            "",
            "## Controls",
            f"- Blocked actions: {totals.get('blocked_actions_count', 0)}",
            f"- Actions proposed/applied/rejected: {totals.get('actions_proposed_count', 0)}/"
            f"{totals.get('actions_applied_count', 0)}/{totals.get('actions_rejected_count', 0)}",
        ]
    )

    lines.append("")
    lines.append("## Regime Share (seconds)")
    for regime, seconds in sorted((totals.get("regime_time_share") or {}).items()):
        lines.append(f"- {regime}: {seconds:.0f}")

    lines.append("")
    lines.append("## Top Block Reasons")
    for reason, count in (totals.get("top_block_reasons") or {}).items():
        lines.append(f"- {reason}: {count}")

    lines.append("")
    lines.append("## ML Snapshots")
    if ml_stats:
        lines.append(f"- Snapshots: {ml_stats.get('snapshot_count', 0)}")
        if ml_stats.get("blocked_avg") is not None:
            lines.append(f"- Avg blocked entries: {ml_stats.get('blocked_avg'):.2f}")
        lines.append(f"- Drift warnings: {ml_stats.get('drift_warnings', 0)}")
        if ml_stats.get("model_versions"):
            lines.append(f"- Model versions: {ml_stats.get('model_versions')}")
    else:
        lines.append("- No ML telemetry snapshots found.")

    lines.append("")
    lines.append("## Policy Changes")
    if policy_changes:
        for change in policy_changes:
            lines.append(f"- {change.get('version_id')} @ {change.get('created_at')} reason={change.get('reason')}")
    else:
        lines.append("- None")

    return "\n".join(lines) + "\n"


def _top_reasons(reasons: Dict[str, int], limit: int = 5) -> Dict[str, int]:
    return dict(sorted(reasons.items(), key=lambda item: item[1], reverse=True)[:limit])


def _parse_iso(value: object) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text.replace("Z", "+00:00")
    return datetime.fromisoformat(text)
