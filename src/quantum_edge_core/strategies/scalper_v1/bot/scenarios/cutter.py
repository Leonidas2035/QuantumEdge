"""Episode cutter for scenario datasets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bot.ml.features import builder as feature_builder

from .features_proxy import compute_metrics
from .io import Tick
from .specs import ScenarioSpec, build_scenarios


@dataclass
class CutterConfig:
    window_ticks: int
    pre_roll_ticks: int
    post_roll_ticks: int
    min_event_ticks: int
    warmup_ticks: int
    stride_ticks: int
    max_episodes_per_scenario: int
    output_format: str = "csv"


@dataclass
class Episode:
    episode_id: str
    scenario_id: str
    ticks: List[Tick]
    metrics: Dict[str, object]


def cut_scenarios(
    ticks: List[Tick],
    config: CutterConfig,
    thresholds: Dict[str, object],
    max_total_episodes: int,
) -> Dict[str, List[Episode]]:
    scenarios = build_scenarios(thresholds)
    outputs: Dict[str, List[Episode]] = {spec.scenario_id: [] for spec in scenarios}
    if not ticks or len(ticks) < config.min_event_ticks:
        return outputs

    idx = max(config.warmup_ticks, config.pre_roll_ticks)
    total = 0
    while idx + config.window_ticks <= len(ticks) and total < max_total_episodes:
        event_start = idx
        event_end = idx + config.window_ticks
        event_ticks = ticks[event_start:event_end]
        if len(event_ticks) < config.min_event_ticks:
            break
        metrics = compute_metrics(event_ticks).to_dict()
        metrics["depth_available"] = bool(metrics.get("depth_available"))
        match = _select_scenario(metrics, scenarios)
        if match is None:
            idx += config.stride_ticks
            continue
        spec, score, reasons = match
        if len(outputs[spec.scenario_id]) >= config.max_episodes_per_scenario:
            idx += config.stride_ticks
            continue
        seg_start = max(0, event_start - config.pre_roll_ticks)
        seg_end = min(len(ticks), event_end + config.post_roll_ticks)
        episode_ticks = ticks[seg_start:seg_end]
        episode_id = f"ep_{len(outputs[spec.scenario_id]) + 1:05d}"
        metrics["score"] = score
        metrics["reasons"] = reasons
        outputs[spec.scenario_id].append(
            Episode(
                episode_id=episode_id,
                scenario_id=spec.scenario_id,
                ticks=episode_ticks,
                metrics=metrics,
            )
        )
        total += 1
        idx = seg_end
    return outputs


def build_schema_payload() -> Dict[str, object]:
    return {
        "raw_columns": ["ts_ms", "price", "qty", "side", "bid", "ask", "depth_usd"],
        "feature_schema_version": feature_builder.schema_version(),
        "feature_names": feature_builder.feature_names(),
        "feature_schema_hash": feature_builder.schema_hash(),
    }


def build_manifest(
    symbol: str,
    spec: ScenarioSpec,
    episodes: List[Episode],
    config: CutterConfig,
    label_horizons: List[int],
    depth_available: bool,
) -> Dict[str, object]:
    return {
        "symbol": symbol,
        "scenario_id": spec.scenario_id,
        "scenario_name": spec.name,
        "scenario_intent": spec.intent,
        "constraints": spec.constraints,
        "episodes": [
            {
                "episode_id": ep.episode_id,
                "file": f"episodes/{ep.episode_id}.{config.output_format}",
                "start_ts_ms": ep.ticks[0].ts_ms,
                "end_ts_ms": ep.ticks[-1].ts_ms,
                "rows": len(ep.ticks),
            }
            for ep in episodes
        ],
        "skipped": len(episodes) == 0,
        "skip_reason": _skip_reason(spec, episodes, depth_available),
        "schema_hash": feature_builder.schema_hash(),
        "label_horizons": label_horizons,
        "build": {
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "git_commit": _git_commit(Path(__file__).resolve().parents[2]),
        },
        "cutter_config": {
            "window_ticks": config.window_ticks,
            "pre_roll_ticks": config.pre_roll_ticks,
            "post_roll_ticks": config.post_roll_ticks,
            "min_event_ticks": config.min_event_ticks,
            "warmup_ticks": config.warmup_ticks,
            "stride_ticks": config.stride_ticks,
            "max_episodes_per_scenario": config.max_episodes_per_scenario,
        },
    }


def build_stats(episodes: List[Episode]) -> Dict[str, object]:
    if not episodes:
        return {}
    fields = [
        "spread_bps_mean",
        "vol_bps",
        "range_bps",
        "tick_rate",
        "imbalance",
        "burstiness",
        "gap_bps_max",
    ]
    stats: Dict[str, List[float]] = {f: [] for f in fields}
    missing = {f: 0 for f in fields}
    for ep in episodes:
        for f in fields:
            val = ep.metrics.get(f)
            if val is None:
                missing[f] += 1
                continue
            stats[f].append(float(val))
    return {
        "episodes": len(episodes),
        "metrics": {f: _summarize(stats[f]) for f in fields},
        "missing_pct": {f: missing[f] / len(episodes) for f in fields},
    }


def _select_scenario(
    metrics: Dict[str, object], specs: List[ScenarioSpec]
) -> Optional[Tuple[ScenarioSpec, float, List[str]]]:
    best: Optional[Tuple[ScenarioSpec, float, List[str]]] = None
    for spec in specs:
        ok, score, reasons = spec.evaluate(metrics)
        if not ok:
            continue
        if best is None or score > best[1]:
            best = (spec, score, reasons)
    return best


def _skip_reason(
    spec: ScenarioSpec, episodes: List[Episode], depth_available: bool
) -> Optional[str]:
    if episodes:
        return None
    if spec.requires_depth and not depth_available:
        return "SKIP_DEPTH_REQUIRED"
    return "SKIP_NOT_ENOUGH_DATA"


def _summarize(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0}
    vals = sorted(values)
    mid = vals[len(vals) // 2]
    return {
        "mean": sum(vals) / len(vals),
        "median": mid,
        "min": vals[0],
        "max": vals[-1],
    }


def _git_commit(root: Path) -> str:
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        sha = (result.stdout or "").strip()
        return sha if sha else "unknown"
    except Exception:
        return "unknown"
