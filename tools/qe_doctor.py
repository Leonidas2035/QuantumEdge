from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tools.qe_config_loader import load_yaml
from tools.qe_paths import ensure_dirs, get_paths


@dataclass
class CheckResult:
    status: str
    message: str
    details: Optional[Dict[str, Any]] = None


def _add(results: List[CheckResult], status: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
    results.append(CheckResult(status=status, message=message, details=details))


def _probe_url(url: str, timeout: float = 2.0) -> Optional[int]:
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status
    except HTTPError as exc:
        return exc.code
    except URLError:
        return None


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def _check_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".qe_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return data


def _get_supervisor_url(config: Dict[str, Any]) -> tuple[str, str, int]:
    env_host = os.getenv("SUPERVISOR_HOST") or os.getenv("QE_SUPERVISOR_HOST")
    env_port = os.getenv("SUPERVISOR_PORT") or os.getenv("QE_SUPERVISOR_PORT")
    env_url = os.getenv("SUPERVISOR_URL")
    supervisor_cfg = config.get("supervisor", {}) if isinstance(config.get("supervisor"), dict) else {}
    host = env_host or supervisor_cfg.get("host") or "127.0.0.1"
    port_raw = env_port or supervisor_cfg.get("port", 8765)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 8765
    url = env_url or supervisor_cfg.get("url") or f"http://{host}:{port}"
    return str(url), str(host), port


def _read_policy(policy_path: Path) -> Optional[Dict[str, Any]]:
    if not policy_path.exists():
        return None
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _policy_fresh(policy: Dict[str, Any]) -> tuple[bool, str]:
    try:
        ts = int(policy.get("ts"))
        ttl = int(policy.get("ttl_sec"))
    except (TypeError, ValueError):
        return False, "missing ts/ttl_sec"
    now = int(time.time())
    age = now - ts
    if now <= ts + ttl:
        return True, f"age={age}s ttl={ttl}s"
    return False, f"expired age={age}s ttl={ttl}s"


def _collect_symbols(bot_cfg: Dict[str, Any]) -> List[str]:
    app_cfg = bot_cfg.get("app", {}) if isinstance(bot_cfg.get("app"), dict) else {}
    mode = str(app_cfg.get("mode", "paper")).lower()
    exchange = str(app_cfg.get("exchange", "")).lower()
    if mode == "demo":
        if exchange == "bingx_swap":
            return [str(s).replace("-", "").upper() for s in (bot_cfg.get("bingx_demo", {}) or {}).get("symbols", [])]
        return [str(s).upper() for s in (bot_cfg.get("binance_demo", {}) or {}).get("symbols", [])]
    return [str(s).upper() for s in (bot_cfg.get("binance", {}) or {}).get("symbols", [])]


def _required_envs(bot_cfg: Dict[str, Any], supervisor_cfg: Dict[str, Any]) -> List[str]:
    required: List[str] = []
    app_cfg = bot_cfg.get("app", {}) if isinstance(bot_cfg.get("app"), dict) else {}
    mode = str(app_cfg.get("mode", "paper")).lower()
    exchange = str(app_cfg.get("exchange", "")).lower()
    if mode in {"demo", "live"}:
        if exchange == "bingx_swap":
            required.extend(["BINGX_DEMO_API_KEY", "BINGX_DEMO_API_SECRET"])
        else:
            required.extend(["BINANCE_DEMO_API_KEY", "BINANCE_DEMO_API_SECRET"])
    if bool(app_cfg.get("llm_enabled", False)):
        required.append("OPENAI_API_KEY")
    llm_cfg = supervisor_cfg.get("llm", {}) if isinstance(supervisor_cfg.get("llm"), dict) else {}
    if bool(llm_cfg.get("enabled", False)):
        required.append(str(llm_cfg.get("api_key_env", "OPENAI_API_KEY_SUPERVISOR")))
    return sorted(set(required))


def _check_models(runtime_models_dir: Path, symbols: List[str], horizons: List[int]) -> tuple[List[str], List[str]]:
    failures: List[str] = []
    warnings: List[str] = []
    for symbol in symbols:
        for horizon in horizons:
            manifest = runtime_models_dir / symbol / str(horizon) / "current" / "manifest.json"
            if not manifest.exists():
                failures.append(f"{symbol} h{horizon}: manifest_missing")
                continue
            try:
                data = _load_json(manifest)
                files = data.get("files", {})
                model_info = files.get("model", {})
                model_rel = Path(str(model_info.get("path", "")))
                if not model_rel.name:
                    failures.append(f"{symbol} h{horizon}: model_path_missing")
                    continue
                model_path = manifest.parent / model_rel
                if not model_path.exists():
                    failures.append(f"{symbol} h{horizon}: model_missing")
                    continue
                sha_expected = str(model_info.get("sha256", ""))
                if not sha_expected:
                    failures.append(f"{symbol} h{horizon}: sha_missing")
                    continue
                sha_actual = _sha256_file(model_path)
                if sha_actual != sha_expected:
                    failures.append(f"{symbol} h{horizon}: sha_mismatch")
                artifact = data.get("artifact")
                if not isinstance(artifact, dict):
                    warnings.append(f"{symbol} h{horizon}: compat_metadata_missing")
                else:
                    missing = [key for key in ("python", "platform", "serializer") if not artifact.get(key)]
                    if missing:
                        warnings.append(f"{symbol} h{horizon}: compat_metadata_incomplete({','.join(missing)})")
                    lib_versions = artifact.get("lib_versions")
                    if lib_versions is None:
                        warnings.append(f"{symbol} h{horizon}: compat_lib_versions_missing")
                if not data.get("model_format"):
                    warnings.append(f"{symbol} h{horizon}: model_format_missing")
                if not data.get("model_api"):
                    warnings.append(f"{symbol} h{horizon}: model_api_missing")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{symbol} h{horizon}: manifest_invalid ({exc})")
    return failures, warnings


def run_doctor(json_output: bool = False) -> int:
    results: List[CheckResult] = []
    paths = get_paths()
    ensure_dirs(paths)

    config_dir = paths["config_dir"]
    config_paths = {
        "quantumedge": config_dir / "quantumedge.yaml",
        "supervisor": config_dir / "supervisor.yaml",
        "bot": config_dir / "bot.yaml",
        "meta_agent": config_dir / "meta_agent.yaml",
    }
    _add(results, "PASS", f"Repo root: {paths['qe_root']}")

    missing_configs = [name for name, path in config_paths.items() if not path.exists()]
    if missing_configs:
        _add(results, "FAIL", f"Missing config(s): {', '.join(missing_configs)}")
    else:
        _add(results, "PASS", "All required config files present")

    runtime_ok = _check_writable(paths["runtime_dir"])
    artifacts_ok = _check_writable(paths["artifacts_dir"])
    if runtime_ok:
        _add(results, "PASS", f"runtime/ writable ({paths['runtime_dir']})")
    else:
        _add(results, "FAIL", f"runtime/ not writable ({paths['runtime_dir']})")
    if artifacts_ok:
        _add(results, "PASS", f"artifacts/ writable ({paths['artifacts_dir']})")
    else:
        _add(results, "FAIL", f"artifacts/ not writable ({paths['artifacts_dir']})")

    try:
        qe_cfg = load_yaml(config_paths["quantumedge"])
    except Exception:
        qe_cfg = {}
    try:
        supervisor_cfg = load_yaml(config_paths["supervisor"])
    except Exception:
        supervisor_cfg = {}
    try:
        bot_cfg = load_yaml(config_paths["bot"])
    except Exception:
        bot_cfg = {}

    supervisor_url, host, port = _get_supervisor_url(qe_cfg)
    health_path = "/api/v1/dashboard/health"
    if isinstance(supervisor_cfg.get("health_path"), str):
        health_path = str(supervisor_cfg["health_path"])
    health_url = f"{supervisor_url.rstrip('/')}/{health_path.lstrip('/')}"
    status = _probe_url(health_url)
    if status is not None and 200 <= status < 300:
        _add(results, "PASS", f"Supervisor reachable ({health_url} -> {status})")
    else:
        note = status if status is not None else "no response"
        _add(results, "FAIL", f"Supervisor unreachable ({health_url} -> {note})")

    if _port_open(host, port):
        _add(results, "PASS", f"Supervisor port open {host}:{port}")
    else:
        _add(results, "FAIL", f"Supervisor port closed {host}:{port}")

    policy_path = paths["runtime_dir"] / "policy.json"
    policy = _read_policy(policy_path)
    if not policy:
        _add(results, "FAIL", f"Policy missing or invalid ({policy_path})")
    else:
        fresh, note = _policy_fresh(policy)
        if fresh:
            _add(results, "PASS", f"Policy fresh ({note})")
        else:
            _add(results, "FAIL", f"Policy stale ({note})")

    horizons = bot_cfg.get("ml", {}).get("horizons", [1, 5, 30])
    try:
        horizons_list = [int(h) for h in horizons]
    except Exception:
        horizons_list = [1, 5, 30]
    symbols = _collect_symbols(bot_cfg)
    if not symbols:
        _add(results, "WARN", "No symbols configured for model check")
    else:
        model_failures, model_warnings = _check_models(paths["runtime_dir"] / "models", symbols, horizons_list)
        if model_failures:
            _add(results, "FAIL", f"Model validation failures: {', '.join(model_failures)}")
        else:
            _add(results, "PASS", "Runtime models present and valid")
        if model_warnings:
            _add(results, "WARN", f"Model compatibility metadata warnings: {', '.join(model_warnings)}")

    telemetry_url = f"{supervisor_url.rstrip('/')}/api/v1/telemetry/summary"
    telemetry_status = _probe_url(telemetry_url)
    if telemetry_status is not None and 200 <= telemetry_status < 300:
        _add(results, "PASS", f"Telemetry summary reachable ({telemetry_status})")
    else:
        note = telemetry_status if telemetry_status is not None else "no response"
        _add(results, "FAIL", f"Telemetry summary unreachable ({telemetry_url} -> {note})")

    required_envs = _required_envs(bot_cfg, supervisor_cfg)
    missing_envs = [name for name in required_envs if not os.getenv(name)]
    if not required_envs:
        _add(results, "PASS", "No required env vars for current mode")
    elif missing_envs:
        _add(results, "FAIL", f"Missing env vars: {', '.join(missing_envs)}")
    else:
        _add(results, "PASS", "Required env vars present")

    failed = [r for r in results if r.status == "FAIL"]
    exit_code = 1 if failed else 0

    if json_output:
        payload = {
            "status": "FAIL" if failed else "PASS",
            "results": [r.__dict__ for r in results],
        }
        print(json.dumps(payload, indent=2))
        return exit_code

    for item in results:
        print(f"[{item.status}] {item.message}")
    print(f"Summary: {len(results)} checks, {len(failed)} FAIL")
    return exit_code
