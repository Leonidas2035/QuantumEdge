import os
import sys
from pathlib import Path
from typing import Dict

from bot.core.secret_store import encrypt_secrets

# Same keys as original version
SECRET_KEYS = [
    "BINGX_DEMO_API_KEY",
    "BINGX_DEMO_API_SECRET",
    
]

def load_env_secrets() -> Dict[str, str]:
    """
    Loads secrets from environment variables instead of prompting the user.
    Fails cleanly if any variable is missing.
    """
    missing = []
    secrets = {}

    for key in SECRET_KEYS:
        val = os.environ.get(key)
        if not val:
            missing.append(key)
        else:
            secrets[key] = val.strip()

    if missing:
        print("[ERROR] Missing required environment variables:")
        for m in missing:
            print("  -", m)
        print("\nSet them using:")
        print("  setx VARIABLE_NAME \"value\"   # Windows")
        sys.exit(1)

    return secrets


def load_passphrase() -> str:
    """
    Reads the encryption passphrase from environment variable.
    """
    pwd = os.environ.get("SCALPER_SECRETS_PASSPHRASE")
    if not pwd:
        print("[ERROR] SCALPER_SECRETS_PASSPHRASE not set.")
        print("Set it using: setx SCALPER_SECRETS_PASSPHRASE \"your_password\"")
        sys.exit(1)
    return pwd.strip()


def main():
    print("=== Secrets Initialization (Non-interactive mode) ===")

    password = load_passphrase()
    secrets = load_env_secrets()

    target = encrypt_secrets(password, secrets)

    print("\n[OK] Encrypted secrets saved to:", target)
    print("[INFO] This version uses only environment variables.")
    print("[INFO] Suitable for SupervisorAgent automatic launches.")


if __name__ == "__main__":
    main()
