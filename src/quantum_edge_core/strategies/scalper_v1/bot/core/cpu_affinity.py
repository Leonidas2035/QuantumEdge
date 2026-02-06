"""Linux CPU affinity helpers (P-core focused, HT-avoidant)."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class CpuAffinityConfig:
    enabled: bool
    mode: str
    explicit_cpus: List[int]
    max_cores: int


@dataclass
class WorkerLimits:
    marketdata: int
    feature: int
    execution: int
    supervisor_web: int


def load_cpu_affinity_config(cfg) -> CpuAffinityConfig:
    raw = cfg.get("cpu", {}) or {}
    enabled = _parse_bool(os.getenv("CPU_PIN_ENABLE"), bool(raw.get("pin_enable", False)))
    mode = str(os.getenv("CPU_PIN_MODE") or raw.get("pin_mode", "auto_pcores")).lower()
    explicit = str(os.getenv("CPU_PIN_CPUS") or raw.get("pin_cpus", "")).strip()
    explicit_cpus = _parse_cpu_list(explicit)
    max_cores = _parse_int(os.getenv("CPU_PIN_MAX_CORES"), int(raw.get("pin_max_cores", 0) or 0))
    return CpuAffinityConfig(
        enabled=enabled,
        mode=mode,
        explicit_cpus=explicit_cpus,
        max_cores=max_cores,
    )


def load_worker_limits(cfg) -> WorkerLimits:
    raw = cfg.get("workers", {}) or {}
    return WorkerLimits(
        marketdata=_parse_int(os.getenv("WORKERS_MARKETDATA"), int(raw.get("marketdata", 0) or 0)),
        feature=_parse_int(os.getenv("WORKERS_FEATURE"), int(raw.get("feature", 0) or 0)),
        execution=_parse_int(os.getenv("WORKERS_EXECUTION"), int(raw.get("execution", 0) or 0)),
        supervisor_web=_parse_int(os.getenv("WORKERS_SUPERVISOR_WEB"), int(raw.get("supervisor_web", 0) or 0)),
    )


def apply_cpu_affinity(cfg: CpuAffinityConfig, logger: Optional[logging.Logger] = None, pid: int = 0) -> List[int]:
    logger = logger or logging.getLogger("cpu_affinity")
    if not cfg.enabled:
        return []
    if sys.platform != "linux":
        logger.warning("CPU affinity requested but platform is %s; skipping.", sys.platform)
        return []
    if not hasattr(os, "sched_setaffinity"):
        logger.warning("CPU affinity not supported in this Python build; skipping.")
        return []

    cpus: List[int] = []
    if cfg.mode == "explicit":
        cpus = cfg.explicit_cpus
    else:
        cpus = _detect_p_cores(cfg.max_cores)

    if not cpus:
        logger.warning("CPU affinity enabled but no CPUs selected; skipping.")
        return []

    try:
        os.sched_setaffinity(pid, set(cpus))
        logger.info("Pinned process %s to CPUs: %s", pid, ",".join(str(c) for c in cpus))
        return cpus
    except Exception as exc:
        logger.warning("Failed to set CPU affinity: %s", exc)
        return []


def _detect_p_cores(max_cores: int) -> List[int]:
    cpu_base = Path("/sys/devices/system/cpu")
    cores: Dict[str, Dict[str, object]] = {}
    for cpu_dir in cpu_base.glob("cpu[0-9]*"):
        cpu_id = _parse_int(cpu_dir.name.replace("cpu", ""), -1)
        if cpu_id < 0:
            continue
        core_id = _read_text(cpu_dir / "topology/core_id")
        siblings = _read_text(cpu_dir / "topology/thread_siblings_list")
        freq = _read_int(cpu_dir / "cpufreq/cpuinfo_max_freq")
        if core_id is None or freq is None:
            continue
        entry = cores.setdefault(core_id, {"freq": freq, "cpus": [], "siblings": siblings})
        entry["freq"] = max(int(entry["freq"]), freq)
        entry["cpus"].append(cpu_id)

    # Choose one CPU per core (avoid HT siblings)
    selected = []
    for core_id, entry in cores.items():
        cpus = sorted(entry.get("cpus") or [])
        if cpus:
            selected.append((entry.get("freq", 0), cpus[0]))
    selected.sort(key=lambda item: item[0], reverse=True)

    cpu_ids = [cpu_id for _, cpu_id in selected]
    if max_cores and max_cores > 0:
        cpu_ids = cpu_ids[: max_cores]
    return cpu_ids


def _parse_cpu_list(value: str) -> List[int]:
    if not value:
        return []
    cpus: List[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            try:
                start = int(start_s)
                end = int(end_s)
            except ValueError:
                continue
            cpus.extend(list(range(start, end + 1)))
        else:
            try:
                cpus.append(int(part))
            except ValueError:
                continue
    return sorted(set(cpus))


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _read_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _parse_int(value: Optional[str], default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
