import re

MASK = "***REDACTED***"

SENSITIVE_KEYWORDS = (
    "API_KEY",
    "APIKEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)

KEY_PATTERN = "|".join(SENSITIVE_KEYWORDS)

ASSIGNMENT_RE = re.compile(
    rf"(?im)^(?P<key>[A-Z0-9_\-]*({KEY_PATTERN})[A-Z0-9_\-]*)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>['\"]?)"
    r"(?P<val>[^#\r\n]*?)"
    r"(?P=quote)"
    r"(?P<tail>\s*(#.*)?)$"
)

JSON_RE = re.compile(
    rf'(?i)("(?P<key>[A-Z0-9_\-]*({KEY_PATTERN})[A-Z0-9_\-]*)"\s*:\s*")'
    r'(?P<val>[^"]+)"'
)

TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")

HIGH_ENTROPY_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9_/\+=\-]{24,})(?![A-Za-z0-9])"
)

PLACEHOLDER_RE = re.compile(
    r"(?i)\b(change_me|your_|replace|example|placeholder|none|null)\b"
)


def _is_placeholder(value: str) -> bool:
    if not value:
        return True
    return bool(PLACEHOLDER_RE.search(value))


def _mask_assignment(match: re.Match) -> str:
    value = (match.group("val") or "").strip()
    if _is_placeholder(value):
        return match.group(0)
    return f"{match.group('key')}{match.group('sep')}{match.group('quote')}{MASK}{match.group('quote')}{match.group('tail') or ''}"


def mask_secrets(text: str) -> str:
    if not text:
        return text

    masked = ASSIGNMENT_RE.sub(_mask_assignment, text)
    masked = JSON_RE.sub(lambda m: f'{m.group(1)}{MASK}"', masked)
    masked = TOKEN_RE.sub("sk-" + MASK, masked)

    def _mask_entropy(match: re.Match) -> str:
        value = match.group(1)
        if _is_placeholder(value):
            return value
        return MASK

    masked = HIGH_ENTROPY_RE.sub(_mask_entropy, masked)
    return masked
