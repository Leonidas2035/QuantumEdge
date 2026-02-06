"""Configuration loader for process orchestration specs."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml

from supervisor.process_spec import HealthCheckSpec, ProcessSpec, RestartPolicySpec


def load_processes_spec(path: Path, base_dir: Path) -> Dict[str, ProcessSpec]:
    raw = _load_yaml(path)
    version = int(raw.get("version", 1))
    if version != 1:
        raise ValueError(f"Unsupported processes spec version: {version}")
    defaults = raw.get("defaults") or {}
    processes = raw.get("processes")
    if not isinstance(processes, dict) or not processes:
        raise ValueError("processes.yaml must define a 'processes' mapping")

    default_env = _coerce_env(defaults.get("env", {}) or {})
    default_restart = _parse_restart(defaults.get("restart", {}) or {}, RestartPolicySpec())
    specs: Dict[str, ProcessSpec] = {}
    for key, value in processes.items():
        if not isinstance(value, dict):
            raise ValueError(f"Process '{key}' must be a mapping")
        name = str(value.get("name") or key)
        if name != key:
            raise ValueError(f"Process key '{key}' does not match name '{name}'")
        if name in specs:
            raise ValueError(f"Duplicate process name: {name}")
        enabled = bool(value.get("enabled", True))
        cwd_raw = value.get("cwd")
        if not cwd_raw:
            raise ValueError(f"Process '{name}' missing cwd")
        cwd = _resolve_path(str(cwd_raw), base_dir)
        if not cwd.exists():
            raise ValueError(f"Process '{name}' cwd does not exist: {cwd}")
        cmd = value.get("cmd")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(item, str) and item for item in cmd):
            raise ValueError(f"Process '{name}' cmd must be a non-empty list of strings")

        env = {**default_env, **_coerce_env(value.get("env", {}) or {})}
        ports = _coerce_ports(value.get("ports", []) or [])
        health = _parse_health(value.get("healthcheck", {}) or {})
        restart = _parse_restart(value.get("restart", {}) or {}, default_restart)

        specs[name] = ProcessSpec(
            name=name,
            enabled=enabled,
            cwd=cwd,
            cmd=list(cmd),
            env=env,
            ports=ports,
            healthcheck=health,
            restart=restart,
        )
    return specs


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Process spec file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Process spec must be a mapping: {path}")
    return data


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _coerce_env(raw: dict) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return env
    for key, value in raw.items():
        if key is None:
            continue
        env[str(key)] = "" if value is None else str(value)
    return env


def _coerce_ports(raw) -> list[int]:
    ports = []
    if not isinstance(raw, list):
        return ports
    for item in raw:
        try:
            ports.append(int(item))
        except (TypeError, ValueError):
            continue
    return ports


def _parse_restart(raw: dict, base: RestartPolicySpec) -> RestartPolicySpec:
    enabled = bool(raw.get("enabled", base.enabled))
    max_retries = int(raw.get("max_retries", base.max_retries))
    if max_retries < 0:
        max_retries = 0
    backoff = raw.get("backoff_s", base.backoff_s)
    backoff_list = []
    if isinstance(backoff, list):
        for item in backoff:
            try:
                backoff_list.append(float(item))
            except (TypeError, ValueError):
                continue
    if not backoff_list:
        backoff_list = list(base.backoff_s)
    cooldown = float(raw.get("cooldown_s", base.cooldown_s))
    if cooldown < 0:
        cooldown = base.cooldown_s
    return RestartPolicySpec(
        enabled=enabled,
        max_retries=max_retries,
        backoff_s=backoff_list,
        cooldown_s=cooldown,
    )


def _parse_health(raw: dict) -> HealthCheckSpec:
    if not isinstance(raw, dict):
        raw = {}
    hc_type = str(raw.get("type", "none")).lower()
    if hc_type not in {"none", "http", "tcp"}:
        raise ValueError(f"Invalid healthcheck type: {hc_type}")
    timeout_s = float(raw.get("timeout_s", 2))
    if timeout_s <= 0:
        timeout_s = 2.0
    url = str(raw.get("url")) if raw.get("url") is not None else None
    host = str(raw.get("host")) if raw.get("host") is not None else None
    port = raw.get("port")
    port_val = int(port) if port is not None else None
    if hc_type == "http" and not url:
        raise ValueError("HTTP healthcheck requires url")
    if hc_type == "tcp":
        if not host:
            host = "127.0.0.1"
        if port_val is None:
            raise ValueError("TCP healthcheck requires port")
    return HealthCheckSpec(type=hc_type, url=url, host=host, port=port_val, timeout_s=timeout_s)
