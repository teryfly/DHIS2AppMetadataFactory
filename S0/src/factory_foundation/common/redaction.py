"""Credential detection and redaction (FD-COM-007, FD-AUD-007).

Detects Authorization headers, cookies, tokens, password fields and client
secrets inside arbitrary values and masks them.  Used by the structured logger
and before every audit write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MASK = "***"

# Key names whose values must always be masked when they appear as dict keys.
_SENSITIVE_KEYS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "clientsecret",
    "token",
    "access_token",
    "accesstoken",
    "refreshtoken",
    "refresh_token",
    "apikey",
    "api_key",
    "x-api-key",
    "bearer",
    "credentials",
}

# Inline textual patterns (e.g. inside a free-text message).
_INLINE_PATTERNS: list[tuple[re.Pattern[str], Any]] = [
    # Authorization: Bearer <...>   /   Basic <...>
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:bearer|basic)\s+\S+"), r"\1" + MASK),
    (re.compile(r"(?i)\b(bearer)\s+[a-z0-9._\-]{8,}"), r"\1 " + MASK),
    # Cookie: ...  (mask whole value to next ; or end)
    (re.compile(r"(?i)\b(cookie\s*[:=]\s*)[^;\n]+"), r"\1" + MASK),
    # key=value / key: value for sensitive keys
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|client_secret|api_key|apikey|token|access_token|refresh_token)"
            r"(\s*[:=]\s*)(\S+)"
        ),
        lambda m: f"{m.group(1)}{m.group(2)}{MASK}",
    ),
]


def is_sensitive_key(key: str) -> bool:
    return key.strip().lower() in _SENSITIVE_KEYS


def contains_credential(text: str) -> bool:
    """True when a free-text value appears to embed a credential (FD-AUD-007)."""
    lowered = text.lower()
    if any(p.search(lowered) for p, _ in _INLINE_PATTERNS):
        return True
    return any(
        f'"{k}"' in lowered or f"{k}:" in lowered or f"{k}=" in lowered for k in _SENSITIVE_KEYS
    )


def redact_string(text: str) -> str:
    for pattern, repl in _INLINE_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_value(value: object) -> tuple[object, bool]:
    """Return ``(redacted_value, changed)`` for a JSON-ish structure."""
    changed = False
    if isinstance(value, str):
        red = redact_string(value)
        return red, red != value
    if isinstance(value, dict):
        out: dict[object, object] = {}
        for k, v in value.items():
            if isinstance(k, str) and is_sensitive_key(k):
                out[k] = MASK
                changed = True
            else:
                rv, rc = redact_value(v)
                out[k] = rv
                changed = changed or rc
        return out, changed
    if isinstance(value, (list, tuple)):
        items = []
        for v in value:
            rv, rc = redact_value(v)
            items.append(rv)
            changed = changed or rc
        return (items if isinstance(value, list) else tuple(items)), changed
    return value, False


@dataclass(frozen=True)
class RedactionResult:
    value: object
    redacted: bool

    @property
    def as_dict(self) -> dict[str, object]:
        return {"value": self.value, "redacted": self.redacted}


def redact_structure(value: object) -> RedactionResult:
    rv, rc = redact_value(value)
    return RedactionResult(rv, rc)
