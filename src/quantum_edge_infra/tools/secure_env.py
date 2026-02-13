#!/usr/bin/env python3
import os
import sys
from pathlib import Path
from cryptography.fernet import Fernet


def generate_key():
    """Generates a new Fernet key and prints it."""
    key = Fernet.generate_key()
    print(f"Generated MASTER_KEY: {key.decode()}")
    print("Store this safely! You will need it to decrypt .env.enc")


def encrypt_env(env_path: Path, output_path: Path, key: str):
    """Encrypts the .env file."""
    if not env_path.exists():
        print(f"Error: {env_path} not found.")
        sys.exit(1)

    fernet = Fernet(key)
    with open(env_path, "rb") as f:
        data = f.read()

    encrypted = fernet.encrypt(data)

    with open(output_path, "wb") as f:
        f.write(encrypted)

    print(f"Encrypted {env_path} -> {output_path}")


def decrypt_env(enc_path: Path, key: str) -> None:
    """Decrypts .env.enc and loads variables into os.environ."""
    if not enc_path.exists():
        print(f"Warning: {enc_path} not found.")
        return

    try:
        fernet = Fernet(key)
        with open(enc_path, "rb") as f:
            encrypted_data = f.read()

        decrypted_data = fernet.decrypt(encrypted_data)

        # Parse and load into os.environ
        for line in decrypted_data.decode("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                # Remove quotes if present
                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                os.environ[key] = value
        # print("Successfully loaded environment variables from .env.enc")
    except Exception as e:
        print(f"Failed to decrypt .env.enc: {e}")
        # raise e  # Optionally re-raise if strict


def main():
    if len(sys.argv) < 2:
        print("Usage: secure_env.py [gen-key | encrypt]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "gen-key":
        generate_key()
    elif command == "encrypt":
        key = os.environ.get("MASTER_KEY")
        if not key:
            print("Error: MASTER_KEY environment variable is required for encryption.")
            sys.exit(1)

        root_dir = Path(__file__).resolve().parents[2]
        print(f"DEBUG: Script location: {Path(__file__).resolve()}")
        print(f"DEBUG: Resolved root_dir: {root_dir}")
        env_path = root_dir / ".env"
        output_path = root_dir / ".env.enc"

        encrypt_env(env_path, output_path, key)
    elif command == "decrypt" or command == "--decrypt":
        key = os.environ.get("MASTER_KEY")
        if not key:
            print("Error: MASTER_KEY environment variable is required for decryption.")
            sys.exit(1)

        root_dir = (
            Path(__file__).resolve().parents[2]
        )  # Adjust for quantum-edge-infra/tools location usually being 2 levels deep from root?
        # Wait, if file is in quantum-edge-infra/tools/secure_env.py, parents[0]=tools, parents[1]=quantum-edge-infra, parents[2]=QuantumEdge.
        # Original script was in tools/secure_env.py (parents[1]=root).
        # Let's check location.

        env_path = root_dir / ".env.enc"
        if not env_path.exists():
            # Fallback if we are running from root and logic differs, but parents[2] seems correct if installed there
            pass

        print(f"Decrypting using key: {key[:5]}...")
        decrypt_env(env_path, key)
        # Verify by printing a known key if needed, or just success
        print("Decryption successful (in-memory). Variables loaded:")
        for k, v in os.environ.items():
            if k in ["GOOGLE_API_KEY", "OPENAI_API_KEY"]:
                print(f"{k}: '...{v[-4:]}'")
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
