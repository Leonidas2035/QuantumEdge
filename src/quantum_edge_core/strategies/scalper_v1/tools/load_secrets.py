"""
Helper script to decrypt and print the encrypted secrets container.

Usage:
    python tools/load_secrets.py [--supervisor-mode]

Password resolution order:
1) SCALPER_SECRETS_PASSPHRASE environment variable
2) If not in supervisor mode, a small Tkinter GUI prompt
"""

import json
import sys
from pathlib import Path

from bot.core.secret_store import (
    SecretsFileNotFound,
    SecretsIntegrityError,
    get_runtime_password,
    is_supervisor_mode,
    load_secrets,
)


def main() -> None:
    supervisor = is_supervisor_mode()
    password = get_runtime_password(supervisor)

    try:
        secrets = load_secrets(password, secret_file=Path(__file__).resolve().parents[1] / "config" / "secrets.enc")
    except SecretsFileNotFound:
        print("[ERROR] Encrypted secrets file not found. Run tools/init_secrets.py first.")
        sys.exit(1)
    except SecretsIntegrityError as exc:
        print(f"[ERROR] Unable to decrypt secrets: {exc}")
        sys.exit(1)

    print(json.dumps(secrets, indent=2))


if __name__ == "__main__":
    main()
