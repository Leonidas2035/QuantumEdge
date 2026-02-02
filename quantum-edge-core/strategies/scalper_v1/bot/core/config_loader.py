import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass

import yaml

from bot.core.secret_store import (
    SecretsFileNotFound,
    SecretsIntegrityError,
    SecretsNotAvailableError,
    get_runtime_password,
    is_supervisor_mode,
    load_secrets,
)

try:
    from tools.qe_config import get_qe_config, get_qe_paths
except Exception:  # pragma: no cover - fallback for legacy runs
    get_qe_config = None
    get_qe_paths = None


class Config:
    def __init__(self, config_path: str = "config/settings.yaml"):
        self.root = Path(__file__).resolve().parents[2]
        self.qe_root = Path(os.getenv("QE_ROOT") or self.root.parent)
        if get_qe_paths:
            try:
                qe_paths = get_qe_paths()
                self.qe_root = qe_paths.get("qe_root", self.qe_root)
            except Exception:
                pass

        env_path = os.getenv("QE_CONFIG_PATH")
        if env_path:
            effective_path = env_path
        else:
            candidate = self.qe_root / "config" / "bot.yaml"
            effective_path = str(candidate) if candidate.exists() else config_path

        resolved = Path(effective_path)
        if not resolved.is_absolute():
            qe_candidate = self.qe_root / resolved
            resolved = qe_candidate if qe_candidate.exists() else self.root / resolved
        self.config_path = resolved.resolve()
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f) or {}

        env_data_dir = os.getenv("QE_DATA_DIR")
        if env_data_dir:
            self.data.setdefault("app", {})["data_path"] = env_data_dir
        env_runtime_dir = os.getenv("QE_RUNTIME_DIR")
        if env_runtime_dir:
            self.data.setdefault("ops", {})["status_file"] = str(Path(env_runtime_dir) / "bot_status.json")

        print(f"[INFO] Config loaded from: {self.config_path}")

        self.secrets: Dict[str, str] = {}
        self._secrets_loaded = False
        self._secrets_required = self._should_require_secrets()
        if self._secrets_required:
            self._maybe_load_secrets(required=True)
        else:
            print("[INFO] Secrets not required in this mode (paper/mock with llm_disabled).")

    def _prompt_password(self) -> str:
        return get_runtime_password(is_supervisor_mode())

    def _should_require_secrets(self) -> bool:
        mode = str(self.data.get("app", {}).get("mode", "paper")).lower()
        llm_enabled = bool(self.data.get("app", {}).get("llm_enabled", False))
        return mode in {"demo", "live"} or llm_enabled

    def _has_env_credentials(self) -> bool:
        key_candidates = [
            "BINANCE_API_KEY",
            "BINANCE_API_SECRET",
            "BINANCE_DEMO_API_KEY",
            "BINANCE_DEMO_API_SECRET",
            "BINGX_API_KEY",
            "BINGX_API_SECRET",
            "BINGX_DEMO_API_KEY",
            "BINGX_DEMO_API_SECRET",
            "OPENAI_API_KEY",
            "OPENAI_API_KEY_SUPERVISOR",
            "SCALPER_SECRETS_PASSPHRASE",
        ]
        return any(os.getenv(k) for k in key_candidates)

    def _fail(self, message: str) -> None:
        print(f"[ERROR] {message}", file=sys.stderr)
        raise SystemExit(1)

    def _maybe_load_secrets(self, required: bool = False) -> None:
        if self._secrets_loaded:
            return

        secrets_file = self.root / "config" / "secrets.enc"
        if not secrets_file.exists():
            if self._has_env_credentials():
                self._secrets_loaded = True
                return
            if not required and not self._secrets_required:
                return
            self._fail(
                "Encrypted secrets not found. Set SCALPER_SECRETS_PASSPHRASE and run tools/init_secrets.py "
                "or provide API keys via environment variables."
            )

        if not required and not self._secrets_required:
            return

        try:
            password = self._prompt_password()
            secrets = load_secrets(password, secret_file=secrets_file)
        except SecretsFileNotFound as exc:
            if required:
                self._fail(
                    "Encrypted secrets file is missing. Create it with: python tools/init_secrets.py "
                    "or set API keys in environment variables."
                )
            raise SecretsNotAvailableError("Encrypted secrets file missing.") from exc
        except SecretsIntegrityError as exc:
            if required:
                self._fail(
                    "Unable to decrypt secrets. Check SCALPER_SECRETS_PASSPHRASE or recreate secrets with tools/init_secrets.py."
                )
            raise SecretsNotAvailableError("Secrets container corrupted.") from exc
        except SecretsNotAvailableError as exc:
            if required:
                self._fail(str(exc))
            raise

        self.secrets = secrets
        for k, v in secrets.items():
            os.environ.setdefault(k, v)
        self._secrets_loaded = True

    def get(self, path: str, default: Optional[Any] = None):
        keys = path.split(".")
        value: Any = self.data
        for k in keys:
            if k not in value:
                return default
            value = value[k]
        return value

    def secret(self, key: str) -> Optional[str]:
        if not self._secrets_required and not self._secrets_loaded:
            self._secrets_loaded = True
        if self._secrets_required and not self._secrets_loaded:
            self._maybe_load_secrets(required=True)
        if key in self.secrets:
            return self.secrets.get(key)
        return os.getenv(key)


config = Config()


@dataclass
class SupervisorSettings:
    enabled: bool
    base_url: str
    api_token: str
    heartbeat_interval_s: float
    timeout_s: float
    on_error: str
    risk_enabled: bool
    risk_on_error: str
    risk_log_level: str


@dataclass
class SupervisorSnapshotsSettings:
    enabled: bool
    supervisor_url: str
    endpoint: str
    timeout_ms: int
    poll_interval_seconds: int
    log_to_console: bool
    log_to_file: bool
    log_file: str


def load_supervisor_settings(cfg: Config) -> SupervisorSettings:
    data = cfg.get("supervisor", {}) or {}
    env_url = os.getenv("SUPERVISOR_URL")
    env_host = os.getenv("SUPERVISOR_HOST")
    env_port = os.getenv("SUPERVISOR_PORT")
    qe_host = None
    qe_port = None
    if get_qe_config:
        try:
            qe_cfg = get_qe_config()
            qe_host = qe_cfg.get("supervisor", {}).get("host")
            qe_port = qe_cfg.get("supervisor", {}).get("port")
        except Exception:
            pass
    host = env_host or qe_host or "127.0.0.1"
    port = env_port or qe_port or 8765
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        port_int = 8765
    base_url = env_url or data.get("base_url") or f"http://{host}:{port_int}"
    return SupervisorSettings(
        enabled=bool(data.get("enabled", False)),
        base_url=str(base_url),
        api_token=str(data.get("api_token", "")),
        heartbeat_interval_s=float(data.get("heartbeat_interval_s", 5.0)),
        timeout_s=float(data.get("timeout_s", 1.0)),
        on_error=str(data.get("on_error", "log_and_continue")),
        risk_enabled=bool(data.get("risk_enabled", True)),
        risk_on_error=str(data.get("risk_on_error", "bypass")),
        risk_log_level=str(data.get("risk_log_level", "info")),
    )


def load_supervisor_snapshot_settings(cfg: Config) -> SupervisorSnapshotsSettings:
    data = cfg.get("supervisor_snapshots", {}) or {}
    env_url = os.getenv("SUPERVISOR_URL")
    supervisor_url = env_url or data.get("supervisor_url", "http://localhost:8000")
    return SupervisorSnapshotsSettings(
        enabled=bool(data.get("enabled", False)),
        supervisor_url=str(supervisor_url),
        endpoint=str(data.get("endpoint", "/api/v1/supervisor/snapshot")),
        timeout_ms=int(data.get("timeout_ms", 500)),
        poll_interval_seconds=int(data.get("poll_interval_seconds", 60)),
        log_to_console=bool(data.get("log_to_console", False)),
        log_to_file=bool(data.get("log_to_file", True)),
        log_file=str(data.get("log_file", "logs/supervisor_snapshots.log")),
    )
