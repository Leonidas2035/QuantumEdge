"""Replay runner for LockBotBTC policy + DDN integration."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Optional

import msgspec
import yaml

from quantum_edge_core.lock_bot.contracts.lockbot_control_v1 import (
    ACK_TOPIC,
    CMD_TOPIC,
    STATUS_TOPIC,
)
from quantum_edge_core.lock_bot.config import LockbotConfig
from quantum_edge_core.lock_bot.ddn.config import (
    DDNConfig,
    DDNProfile,
)
from quantum_edge_core.lock_bot.main import LockBotService
from quantum_edge_core.lock_bot.replay.bot_adapter import (
    ReplayBotAdapter,
)
from quantum_edge_core.lock_bot.replay.bus import ReplayBus
from quantum_edge_core.lock_bot.replay.clock import (
    ReplayClock,
)
from quantum_edge_core.lock_bot.replay.metrics import (
    MetricsCollector,
    ReplayFillConfig,
)

ROOT = Path(__file__).resolve().parents[3]
SUPERVISOR_DIR = ROOT / "SupervisorAgent"
if SUPERVISOR_DIR.exists() and str(SUPERVISOR_DIR) not in sys.path:
    sys.path.insert(0, str(SUPERVISOR_DIR))

from supervisor.lockbot.models import PolicyRunnerConfig, load_lockbot_policy_config
from supervisor.lockbot.policy_runner import LockbotPolicyRunner
from supervisor.lockbot.replay.policy_adapter import (
    PolicyReplayAdapter,
    ReplayControlClient,
)


def load_dataset(
    path: Path, *, time_min: Optional[int] = None, time_max: Optional[int] = None
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            ts_event = int(payload.get("ts_event") or 0)
            if time_min is not None and ts_event < time_min:
                continue
            if time_max is not None and ts_event > time_max:
                continue
            events.append(_normalize_event(payload))
    return _sort_events(events)


def run_replay(
    events: Iterable[Dict[str, Any]],
    *,
    out_dir: Path,
    policy_cfg: PolicyRunnerConfig,
    bot_cfg: LockbotConfig,
    ddn_cfg: Optional[DDNConfig] = None,
    tick_s: int = 1,
    realtime: bool = False,
    paper_fill_model: str = "tierA",
    account_topics: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    clock = ReplayClock(realtime=realtime)
    bus = ReplayBus()

    if ddn_cfg:
        bot_cfg.ddn = ddn_cfg
    bot_service = LockBotService(bot_cfg, ipc_enabled=False)
    bot_adapter = ReplayBotAdapter(bot_service, bus, clock)

    policy_cfg.enabled = True
    policy_cfg.audit_log_path = str(out_dir / "policy_decisions.jsonl")

    control_client = ReplayControlClient(
        bus,
        cmd_topic=CMD_TOPIC,
        ack_topic=ACK_TOPIC,
        status_topic=STATUS_TOPIC,
        bot_id=bot_cfg.bot_id,
        symbol=bot_cfg.symbol,
        ttl_ms=bot_cfg.cmd_ttl_ms,
        clock=clock,
    )
    runner = LockbotPolicyRunner(policy_cfg, control_client)
    policy_adapter = PolicyReplayAdapter(
        runner, bus, market_topics=policy_cfg.hub_topics, symbol=policy_cfg.symbol
    )

    recorder = _ReplayRecorder(out_dir / "decisions.jsonl")
    fill_cfg = ReplayFillConfig(tier=paper_fill_model)
    metrics = MetricsCollector(fill_cfg)
    market_topics = set(policy_cfg.hub_topics)
    account_topics = set(account_topics or [f"{bot_cfg.symbol}:position_snapshot"])

    last_mark: Optional[float] = None

    def on_cmd(_topic: str, cmd: Any) -> None:
        payload = _as_dict(cmd)
        metrics.on_command(payload, last_mark)
        recorder.record("cmd", payload)

    def on_ack(_topic: str, ack: Any) -> None:
        payload = _as_dict(ack)
        recorder.record("ack", payload)

    def on_status(_topic: str, status: Any) -> None:
        payload = _as_dict(status)
        metrics.on_status(payload)
        recorder.record("status", payload)

    bus.subscribe(CMD_TOPIC, on_cmd)
    bus.subscribe(ACK_TOPIC, on_ack)
    bus.subscribe(STATUS_TOPIC, on_status)

    ordered = _sort_events([_normalize_event(event) for event in events])
    if not ordered:
        metrics_snapshot = metrics.build()
        _write_outputs(
            out_dir,
            metrics_snapshot,
            metadata or {},
            policy_cfg,
            bot_cfg,
            ddn_cfg,
            paper_fill_model,
        )
        recorder.close()
        return

    next_tick_ms = int(ordered[0]["ts_event"])
    idx = 0
    while idx < len(ordered):
        ts_event = int(ordered[idx]["ts_event"])
        clock.advance_to(ts_event)
        while idx < len(ordered) and int(ordered[idx]["ts_event"]) == ts_event:
            event = ordered[idx]
            topic = str(event.get("topic") or "")
            if not topic:
                idx += 1
                continue
            if topic in market_topics:
                bot_adapter.on_market_event(topic, event)
            elif topic in account_topics:
                payload = (
                    event.get("payload")
                    if isinstance(event.get("payload"), dict)
                    else {}
                )
                bot_adapter.on_account_event(topic, payload)
            if topic == f"{bot_cfg.symbol}:mark_price_1s":
                payload = (
                    event.get("payload")
                    if isinstance(event.get("payload"), dict)
                    else {}
                )
                if payload.get("mark_price") is not None:
                    last_mark = float(payload.get("mark_price"))
            bus.publish(topic, event)
            idx += 1

        while next_tick_ms <= ts_event:
            bot_adapter.emit_status(now_ms=next_tick_ms)
            decision = policy_adapter.tick(next_tick_ms)
            if decision:
                metrics.on_policy_decision(decision)
                recorder.record("policy_decision", decision)
            next_tick_ms += tick_s * 1000

    metrics_snapshot = metrics.build()
    _write_outputs(
        out_dir,
        metrics_snapshot,
        metadata or {},
        policy_cfg,
        bot_cfg,
        ddn_cfg,
        paper_fill_model,
    )
    recorder.close()


def _normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(event)
    topic = payload.get("topic")
    if topic and "event_type" not in payload and ":" in str(topic):
        payload["event_type"] = str(topic).split(":", 1)[1]
    payload.setdefault("schema", "lockbot_md.v1")
    payload.setdefault("source", "replay")
    payload.setdefault("ts_pub", payload.get("ts_event"))
    payload.setdefault("seq", 0)
    return payload


def _sort_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        events,
        key=lambda item: (
            int(item.get("ts_event") or 0),
            int(item.get("seq") or 0),
            str(item.get("topic") or ""),
        ),
    )


def _write_outputs(
    out_dir: Path,
    metrics: Any,
    metadata: Dict[str, Any],
    policy_cfg: PolicyRunnerConfig,
    bot_cfg: LockbotConfig,
    ddn_cfg: Optional[DDNConfig],
    paper_fill_model: str,
) -> None:
    metrics_path = out_dir / "metrics.json"
    with metrics_path.open("w", encoding="utf-8") as handle:
        json.dump(asdict(metrics), handle, indent=2, sort_keys=True)

    run_meta = {
        "git_sha": _git_sha(),
        "policy_config": asdict(policy_cfg),
        "bot_config": {
            "bot_id": bot_cfg.bot_id,
            "symbol": bot_cfg.symbol,
            "market_topics": bot_cfg.market_topics,
            "account_topics": bot_cfg.account_topics,
        },
        "ddn_config": asdict(ddn_cfg) if ddn_cfg else None,
        "paper_fill_model": paper_fill_model,
    }
    run_meta.update(metadata)
    meta_path = out_dir / "run_metadata.json"
    with meta_path.open("w", encoding="utf-8") as handle:
        json.dump(run_meta, handle, indent=2, sort_keys=True)

    summary_path = out_dir / "summary.md"
    with summary_path.open("w", encoding="utf-8") as handle:
        handle.write("# LockBotBTC Replay Summary\n")
        handle.write(f"- git_sha: {run_meta.get('git_sha')}\n")
        handle.write(f"- paper_fill_model: {paper_fill_model}\n")
        handle.write(f"- cmd_counts: {metrics.cmd_counts}\n")
        handle.write(f"- ddn_reject_count: {metrics.ddn_reject_count}\n")
        handle.write(f"- panic_count: {metrics.panic_count}\n")
        handle.write(f"- min_distance_to_liq_bps: {metrics.min_distance_to_liq_bps}\n")
        handle.write(f"- max_margin_usage: {metrics.max_margin_usage}\n")
        handle.write(f"- paper_pnl_est: {metrics.paper_pnl_est}\n")


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _as_dict(message: Any) -> Dict[str, Any]:
    if isinstance(message, dict):
        return message
    try:
        return msgspec.structs.asdict(message)
    except Exception:
        return dict(message)


def load_ddn_config(
    path: Optional[Path], *, base: Optional[DDNConfig] = None
) -> Optional[DDNConfig]:
    if not path:
        return None
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = base if base else DDNConfig.default()
    profiles = {}
    for name, prof in (raw.get("profiles", {}) or {}).items():
        if not isinstance(prof, dict):
            continue
        profiles[name] = DDNProfile(
            name=str(name),
            target=float(prof.get("target", 0.0)),
            band_low=float(prof.get("band_low", -0.1)),
            band_high=float(prof.get("band_high", 0.1)),
            force_hedge=bool(prof.get("force_hedge", False)),
        )
    if profiles:
        cfg.profiles = profiles
    cfg.max_band_abs = float(raw.get("max_band_abs", cfg.max_band_abs))
    cfg.max_margin_usage = float(raw.get("max_margin_usage", cfg.max_margin_usage))
    cfg.min_distance_to_liq_bps = float(
        raw.get("min_distance_to_liq_bps", cfg.min_distance_to_liq_bps)
    )
    cfg.max_step_notional_usd = float(
        raw.get("max_step_notional_usd", cfg.max_step_notional_usd)
    )
    cfg.min_step_notional_usd = float(
        raw.get("min_step_notional_usd", cfg.min_step_notional_usd)
    )
    cfg.max_steps_per_minute = int(
        raw.get("max_steps_per_minute", cfg.max_steps_per_minute)
    )
    cfg.cooldown_ms_after_reject = int(
        raw.get("cooldown_ms_after_reject", cfg.cooldown_ms_after_reject)
    )
    cfg.panic_on_lag_ms = int(raw.get("panic_on_lag_ms", cfg.panic_on_lag_ms))
    cfg.taker_fee_bps = float(raw.get("taker_fee_bps", cfg.taker_fee_bps))
    cfg.maker_fee_bps = float(raw.get("maker_fee_bps", cfg.maker_fee_bps))
    cfg.expected_slippage_bps_market = float(
        raw.get("expected_slippage_bps_market", cfg.expected_slippage_bps_market)
    )
    cfg.funding_weight = float(raw.get("funding_weight", cfg.funding_weight))
    cfg.min_expected_edge_bps = float(
        raw.get("min_expected_edge_bps", cfg.min_expected_edge_bps)
    )
    cfg.max_cost_bps_per_step = float(
        raw.get("max_cost_bps_per_step", cfg.max_cost_bps_per_step)
    )
    cfg.volatility_window = int(raw.get("volatility_window", cfg.volatility_window))
    cfg.step_volatility_scale = float(
        raw.get("step_volatility_scale", cfg.step_volatility_scale)
    )
    return cfg


def load_policy_config(path: Optional[Path]) -> PolicyRunnerConfig:
    if not path:
        return PolicyRunnerConfig(enabled=True)
    return load_lockbot_policy_config(path)


class _ReplayRecorder:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("w", encoding="utf-8")

    def record(self, record_type: str, payload: Dict[str, Any]) -> None:
        self._fh.write(
            json.dumps({"type": record_type, "data": payload}, sort_keys=True) + "\n"
        )

    def close(self) -> None:
        self._fh.close()
