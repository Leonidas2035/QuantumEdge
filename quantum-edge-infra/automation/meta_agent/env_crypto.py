import base64
import json
import os
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


DEFAULT_PASSWORD = "1111"
DEFAULT_SALT_SIZE = 16
DEFAULT_ITERATIONS = 390000


class EnvCryptoError(Exception):
    """Raised when encrypted environment handling fails."""


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=DEFAULT_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _load_env_from_string(env_data: str) -> None:
    for raw_line in env_data.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()


def encrypt_env(
    plain_path: str = ".env",
    enc_path: str = ".env.enc",
    password: str = DEFAULT_PASSWORD,
) -> None:
    """
    Encrypts a plaintext .env file for at-rest storage.
    Stores salt and token as JSON so it is easy to parse later.
    """
    if not os.path.exists(plain_path):
        raise EnvCryptoError(f"Plain env file not found: {plain_path}")

    with open(plain_path, "rb") as handle:
        plaintext = handle.read()

    salt = os.urandom(DEFAULT_SALT_SIZE)
    key = _derive_key(password, salt)
    token = Fernet(key).encrypt(plaintext)

    payload = {
        "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
        "token": token.decode("ascii"),
    }

    with open(enc_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def load_decrypted_env(
    enc_path: str = ".env.enc",
    password: str = DEFAULT_PASSWORD,
) -> None:
    """
    Decrypts the encrypted env file and loads keys into os.environ.
    """
    if not os.path.exists(enc_path):
        raise EnvCryptoError(f"Encrypted env file not found: {enc_path}")

    with open(enc_path, "r", encoding="utf-8") as handle:
        try:
            payload = json.load(handle)
        except json.JSONDecodeError as exc:
            raise EnvCryptoError(f"Invalid encrypted env format: {exc}") from exc

    salt_b64: Optional[str] = payload.get("salt")
    token_b64: Optional[str] = payload.get("token")
    if not salt_b64 or not token_b64:
        raise EnvCryptoError("Encrypted env is missing required fields.")

    try:
        salt = base64.urlsafe_b64decode(salt_b64)
        key = _derive_key(password, salt)
        decrypted_bytes = Fernet(key).decrypt(token_b64.encode("ascii"))
    except Exception as exc:  # broad to surface cryptography errors
        raise EnvCryptoError(f"Failed to decrypt env: {exc}") from exc

    _load_env_from_string(decrypted_bytes.decode("utf-8"))
