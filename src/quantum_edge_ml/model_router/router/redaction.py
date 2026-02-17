from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

REDACT_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9\-\._~\+\/]+=*", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"api_key\s*=\s*[^\s,;]+", re.IGNORECASE),
    re.compile(r"token\s*=\s*[^\s,;]+", re.IGNORECASE),
    re.compile(r"key\s*=\s*[^\s,;]+", re.IGNORECASE),
]


@dataclass
class RedactionResult:
    prompt_hash: str
    prompt_redacted: str


def hash_prompt(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def redact_prompt(
    prompt: str, *, store_prompt: bool, max_len: int = 512
) -> RedactionResult:
    redacted = prompt
    for pattern in REDACT_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)

    redacted = redacted.replace("\r", " ").replace("\n", " ").strip()
    if len(redacted) > max_len:
        redacted = redacted[:max_len]

    if not store_prompt:
        synopsis = redacted[:256]
        if len(redacted) > 256:
            synopsis += "..."
        redacted = synopsis

    return RedactionResult(prompt_hash=hash_prompt(prompt), prompt_redacted=redacted)
