import json
import os
import subprocess
import sys
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class SecretsError(Exception):
    """Base class for secret store errors."""


class SecretsFileNotFound(SecretsError):
    """Raised when the encrypted secrets file is missing."""


class SecretsIntegrityError(SecretsError):
    """Raised when secrets cannot be decrypted (wrong password or tampering)."""


class SecretsNotAvailableError(SecretsError):
    """Raised when secrets cannot be loaded for operational use."""


DEFAULT_ITERATIONS = 200_000
SALT_LEN = 16
SUPERVISOR_MODE_ENV = "QUANTUMEDGE_SUPERVISOR_MODE"


@dataclass
class SecretStorePaths:
    """Holds canonical paths for the secret container."""

    root: Path

    @property
    def secrets_file(self) -> Path:
        return self.root / "config" / "secrets.enc"


def _derive_key(password: str, salt: bytes, iterations: int = DEFAULT_ITERATIONS) -> bytes:
    """Derive a 32-byte key from a password and salt using PBKDF2-HMAC-SHA256."""
    if not password:
        raise ValueError("Password must not be empty.")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return urlsafe_b64encode(kdf.derive(password.encode()))


def encrypt_secrets(password: str, secrets: Dict[str, str], secret_file: Optional[Path] = None) -> Path:
    """Encrypt and persist secrets to disk. Returns the written file path."""
    root = Path(__file__).resolve().parents[2]
    paths = SecretStorePaths(root=root)
    target = secret_file or paths.secrets_file
    target.parent.mkdir(parents=True, exist_ok=True)

    salt = os.urandom(SALT_LEN)
    key = _derive_key(password, salt)
    fernet = Fernet(key)

    payload = json.dumps(secrets, separators=(",", ":")).encode()
    token = fernet.encrypt(payload)

    with open(target, "wb") as fh:
        fh.write(salt + token)

    return target


def load_secrets(password: str, secret_file: Optional[Path] = None) -> Dict[str, str]:
    """Load and decrypt secrets from disk."""
    root = Path(__file__).resolve().parents[2]
    paths = SecretStorePaths(root=root)
    src = secret_file or paths.secrets_file

    if not src.exists():
        raise SecretsFileNotFound(f"Encrypted secrets file not found: {src}")

    raw = src.read_bytes()
    if len(raw) <= SALT_LEN:
        raise SecretsIntegrityError("Secrets container is malformed or empty.")

    salt, token = raw[:SALT_LEN], raw[SALT_LEN:]
    key = _derive_key(password, salt)
    fernet = Fernet(key)

    try:
        decrypted = fernet.decrypt(token)
    except InvalidToken as exc:
        raise SecretsIntegrityError("Secrets container corrupted or password incorrect.") from exc

    try:
        data = json.loads(decrypted.decode())
    except Exception as exc:
        raise SecretsIntegrityError("Unable to parse decrypted secrets.") from exc

    if not isinstance(data, dict):
        raise SecretsIntegrityError("Secrets payload is not a dictionary.")

    return {str(k): str(v) for k, v in data.items()}


def is_supervisor_mode() -> bool:
    """
    Detect whether the process is running under SupervisorAgent control.

    Triggers if the environment variable QUANTUMEDGE_SUPERVISOR_MODE is set to a truthy
    value or when the process is launched with a --supervisor-mode flag.
    """
    env_flag = os.getenv(SUPERVISOR_MODE_ENV, "").strip().lower()
    if env_flag in {"1", "true", "yes", "on"}:
        return True

    for arg in sys.argv[1:]:
        normalized = arg.strip().lower()
        if normalized in {"--supervisor-mode", "--supervisor_mode"}:
            return True
        if normalized.startswith("--supervisor-mode="):
            return True

    # Heuristic: detect if the parent process is SupervisorAgent (best-effort, Windows-friendly).
    try:
        ppid = os.getppid()
        if ppid and os.name == "nt":
            result = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={ppid}", "get", "CommandLine"],
                capture_output=True,
                text=True,
                check=False,
            )
            cmdline = (result.stdout or "") + (result.stderr or "")
            cmdline = cmdline.lower()
            if "supervisor.py" in cmdline or "supervisoragent" in cmdline:
                return True
    except Exception:
        # Non-fatal; fall back to other signals.
        pass
    return False


def get_runtime_password(supervisor_mode: bool) -> str:
    """
    Fetch the runtime secrets password using environment variable first, then GUI prompt.

    Behaviour:
    - If SCALPER_SECRETS_PASSPHRASE exists, return it immediately.
    - If supervisor_mode is True and no env var is set, print an error and exit non-zero.
    - Otherwise, open a minimal Tkinter GUI to capture a visible password entry.
    """
    env_pwd = os.getenv("SCALPER_SECRETS_PASSPHRASE")
    if env_pwd:
        return env_pwd

    if supervisor_mode:
        print(
            "[ERROR] Secrets passphrase is required but SCALPER_SECRETS_PASSPHRASE is not set. "
            "Supervisor mode forbids GUI prompts to avoid blocking; exiting."
        )
        sys.exit(1)

    try:
        from bot.core.gui_password_prompt import prompt_password
    except Exception as exc:  # pragma: no cover - GUI import failure path
        print(f"[ERROR] Unable to start password prompt GUI: {exc}")
        sys.exit(1)

    password = prompt_password()
    if not password:
        print("[ERROR] No password provided. Exiting.")
        sys.exit(1)

    return password
