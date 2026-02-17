"""Auto-tuning logic for Ops Brain v1."""

from __future__ import annotations

import copy
import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from tools.qe_paths import get_paths
from tools.qe_config_loader import load_yaml

from supervisor.ops.config import get_nested


def load_policy_bundle(active_policy: Dict[str, Any]) -> Dict[str, Any]:
    paths = get_paths()
    policy = copy.deepcopy(active_policy)

    bot_cfg_path = Path(paths["config_dir"]) / "bot.yaml"
    bot_cfg = _safe_load_yaml(bot_cfg_path)
    bot_thresholds = (
        ((bot_cfg.get("ml") or {}).get("thresholds") or {})
        if isinstance(bot_cfg, dict)
        else {}
    )

    settings_path = Path(paths["bot_dir"]) / "config" / "settings.yaml"
    settings_cfg = _safe_load_yaml(settings_path)
    order_policy = (
        ((settings_cfg.get("execution") or {}).get("scalp") or {}).get("order_policy")
        or {}
        if isinstance(settings_cfg, dict)
        else {}
    )

    ml_section = policy.setdefault("ml", {})
    if isinstance(ml_section, dict):
        ml_section.setdefault("thresholds", bot_thresholds)

    exec_section = policy.setdefault("execution", {})
    if isinstance(exec_section, dict):
        exec_section.setdefault(
            "passive_offset_bps", order_policy.get("near_touch_offset_bps")
        )
        exec_section.setdefault("passive_ttl_ms", order_policy.get("cancel_timeout_ms"))
        if "max_requotes" in order_policy:
            exec_section.setdefault("max_requotes", order_policy.get("max_requotes"))

    return policy


def collect_metrics(
    runs_dir: Path, telemetry_path: Optional[Path], ops_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    window_runs = int(get_nested(ops_cfg, "autotune.window_runs", 5) or 5)
    summaries = _load_recent_summaries(runs_dir, window_runs)

    wins = sum(_safe_int(s.get("wins")) for s in summaries)
    losses = sum(_safe_int(s.get("losses")) for s in summaries)
    trades = sum(_safe_int(s.get("trades")) for s in summaries)
    blocked = sum(_safe_int(s.get("blocked_actions_count")) for s in summaries)
    errors = sum(_safe_int(s.get("errors_count")) for s in summaries)

    winrate = None
    if wins + losses > 0:
        winrate = wins / max(wins + losses, 1)

    block_rate = blocked / max(trades + blocked, 1)

    telemetry_metrics = _load_telemetry_metrics(
        telemetry_path,
        drift_score_high=float(get_nested(ops_cfg, "autotune.drift_score_high", 3.0)),
    )

    return {
        "runs": len(summaries),
        "wins": wins,
        "losses": losses,
        "trades": trades,
        "blocked": blocked,
        "errors": errors,
        "winrate": winrate,
        "block_rate": block_rate,
        **telemetry_metrics,
    }


def propose_tuning(
    policy: Dict[str, Any],
    metrics: Dict[str, Any],
    ops_cfg: Dict[str, Any],
) -> Tuple[Dict[str, Any], list[Dict[str, Any]], list[str]]:
    bounds = get_nested(ops_cfg, "bounds", {})
    tune_cfg = get_nested(ops_cfg, "autotune", {})
    max_changes = int(tune_cfg.get("max_changes_per_run", 3))
    min_runs = int(tune_cfg.get("min_runs", 2))

    if metrics.get("runs", 0) < min_runs:
        return policy, [], ["insufficient_runs"]

    candidate = copy.deepcopy(policy)
    changes: list[Dict[str, Any]] = []
    notes: list[str] = []

    def apply_change(path: str, new_value: Any) -> None:
        if len(changes) >= max_changes:
            return
        old_value = _set_path(candidate, path, new_value)
        if old_value is not None and old_value != new_value:
            changes.append({"path": path, "old": old_value, "new": new_value})

    block_rate = metrics.get("block_rate", 0.0)
    winrate = metrics.get("winrate")
    trades = metrics.get("trades", 0)
    errors = metrics.get("errors", 0)
    latency_p95 = metrics.get("latency_p95")
    drift_score_max = metrics.get("drift_score_max")

    block_rate_high = float(tune_cfg.get("block_rate_high", 0.4))
    winrate_high = float(tune_cfg.get("winrate_high", 0.6))
    winrate_low = float(tune_cfg.get("winrate_low", 0.45))
    trades_low = int(tune_cfg.get("trades_low", 5))
    errors_max = int(tune_cfg.get("errors_max", 0))
    latency_high = float(tune_cfg.get("latency_ms_high", 500))
    drift_high = float(tune_cfg.get("drift_score_high", 3.0))

    if errors > errors_max:
        notes.append("recent_errors")

    guard_bounds = bounds.get("guards", {}) if isinstance(bounds, dict) else {}
    exec_bounds = bounds.get("execution", {}) if isinstance(bounds, dict) else {}
    ml_bounds = (
        (bounds.get("ml") or {}).get("thresholds") if isinstance(bounds, dict) else {}
    )

    if (
        winrate is not None
        and block_rate >= block_rate_high
        and winrate >= winrate_high
    ):
        current = _get_path(candidate, "guards.spread_bps_max")
        new_val = _nudge(current, +1, guard_bounds.get("spread_bps_max"))
        if new_val is not None:
            apply_change("guards.spread_bps_max", new_val)
            notes.append("relax_spread_for_blocks")

    if latency_p95 is not None and latency_p95 >= latency_high:
        current = _get_path(candidate, "guards.spread_bps_max")
        new_val = _nudge(current, -1, guard_bounds.get("spread_bps_max"))
        if new_val is not None:
            apply_change("guards.spread_bps_max", new_val)
            notes.append("tighten_spread_for_latency")
        ttl_current = _get_path(candidate, "execution.passive_ttl_ms")
        ttl_new = _nudge(ttl_current, +1, exec_bounds.get("passive_ttl_ms"))
        if ttl_new is not None:
            apply_change("execution.passive_ttl_ms", ttl_new)
            notes.append("increase_ttl_for_latency")
        requotes_current = _get_path(candidate, "execution.max_requotes")
        requotes_new = _nudge(requotes_current, -1, exec_bounds.get("max_requotes"))
        if requotes_new is not None:
            apply_change("execution.max_requotes", requotes_new)
            notes.append("reduce_requotes_for_latency")

    if (
        winrate is not None
        and winrate <= winrate_low
        and (drift_score_max is None or drift_score_max <= drift_high)
    ):
        for horizon in ("h1", "h5", "h15"):
            current = _get_path(candidate, f"ml.thresholds.{horizon}")
            new_val = _nudge(current, +1, (ml_bounds or {}).get(horizon))
            if new_val is not None:
                apply_change(f"ml.thresholds.{horizon}", new_val)
        if any(change["path"].startswith("ml.thresholds") for change in changes):
            notes.append("tighten_ml_thresholds")

    if (
        winrate is not None
        and winrate >= winrate_high
        and trades <= trades_low
        and block_rate < block_rate_high
    ):
        for horizon in ("h1", "h5", "h15"):
            current = _get_path(candidate, f"ml.thresholds.{horizon}")
            new_val = _nudge(current, -1, (ml_bounds or {}).get(horizon))
            if new_val is not None:
                apply_change(f"ml.thresholds.{horizon}", new_val)
        if any(change["path"].startswith("ml.thresholds") for change in changes):
            notes.append("relax_ml_thresholds")

    return candidate, changes, notes


def _load_recent_summaries(runs_dir: Path, limit: int) -> list[Dict[str, Any]]:
    summaries: list[Dict[str, Any]] = []
    if not runs_dir.exists():
        return summaries
    run_dirs = sorted(
        [p for p in runs_dir.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for run_dir in run_dirs:
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            continue
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        summaries.append(payload)
        if len(summaries) >= limit:
            break
    return summaries


def _load_telemetry_metrics(
    telemetry_path: Optional[Path], drift_score_high: float
) -> Dict[str, Any]:
    if telemetry_path is None or not telemetry_path.exists():
        return {
            "latency_p95": None,
            "error_events": 0,
            "ml_snapshot_count": 0,
            "drift_score_max": None,
        }

    latency_vals = []
    error_events = 0
    ml_snapshot_count = 0
    drift_score_max = None
    blocked_total = 0

    tail = deque(maxlen=2000)
    try:
        with telemetry_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                tail.append(line)
    except OSError:
        return {
            "latency_p95": None,
            "error_events": 0,
            "ml_snapshot_count": 0,
            "drift_score_max": None,
        }

    for line in tail:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = str(payload.get("type") or "")
        data = payload.get("data") or {}
        if event_type == "latency":
            loop_ms = data.get("loop_ms")
            try:
                latency_vals.append(float(loop_ms))
            except (TypeError, ValueError):
                continue
        elif event_type == "error":
            error_events += 1
        elif event_type == "ml_snapshot":
            ml_snapshot_count += 1
            blocked = data.get("blocked_entries")
            try:
                blocked_total += int(blocked)
            except (TypeError, ValueError):
                pass
            drift = data.get("drift") or {}
            try:
                score = drift.get("score")
                if score is not None:
                    drift_score_max = max(drift_score_max or 0.0, float(score))
            except (TypeError, ValueError):
                pass

    latency_p95 = _percentile(latency_vals, 0.95)
    return {
        "latency_p95": latency_p95,
        "error_events": error_events,
        "ml_snapshot_count": ml_snapshot_count,
        "blocked_avg": (
            (blocked_total / ml_snapshot_count) if ml_snapshot_count else None
        ),
        "drift_score_max": drift_score_max,
        "drift_high": drift_score_max is not None
        and drift_score_max >= drift_score_high,
    }


def _safe_load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return load_yaml(path)
    except Exception:
        return {}


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    values_sorted = sorted(values)
    idx = int(round((len(values_sorted) - 1) * pct))
    return values_sorted[idx]


def _get_path(data: Dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set_path(data: Dict[str, Any], path: str, value: Any) -> Optional[Any]:
    node: Any = data
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    leaf = parts[-1]
    old = node.get(leaf)
    node[leaf] = value
    return old


def _nudge(
    current: Optional[float], direction: int, bounds: Optional[Dict[str, Any]]
) -> Optional[float]:
    if current is None or bounds is None:
        return None
    try:
        step = float(bounds.get("step", 0.0))
        minimum = float(bounds.get("min", current))
        maximum = float(bounds.get("max", current))
    except (TypeError, ValueError):
        return None
    new_val = current + direction * step
    if new_val < minimum:
        new_val = minimum
    if new_val > maximum:
        new_val = maximum
    return new_val
