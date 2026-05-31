"""Redaction helpers for memory outputs and embedding inputs."""

from __future__ import annotations

import json
import re
from typing import Any

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(api\s*key\s*[:=]\s*)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(auth[_-]?token['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(password|passwd|secret|credential)\s*[:=]\s*[^,\s\"'}]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(cookie['\"]?\s*[:=]\s*['\"]?)[^'\"\n]+"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9._-]{8,}\b"), "sk-[REDACTED]"),
    (re.compile(r"postgres(?:ql)?://[^\s\"'<>]+", re.I), "postgresql://[REDACTED_DSN]"),
    (re.compile(r"(?i)\b(dbname=[^\s]+\s+host=)[^\s]+"), r"\1[REDACTED_HOST]"),
    (re.compile(r"(?i)\b(host=)(?:localhost|127\.0\.0\.1|[0-9]{1,3}(?:\.[0-9]{1,3}){3}|[^\s\"']+)"), r"\1[REDACTED_HOST]"),
    (re.compile(r"(?i)\b(port=)(?:5432|15432)\b"), r"\1[REDACTED_PORT]"),
    (re.compile(r"(?i)\b(user=)[^\s\"']+"), r"\1[REDACTED_USER]"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}:(?:5432|15432)\b"), "[REDACTED_DB_ENDPOINT]"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "[REDACTED_IP]"),
]

_PROMPT_NOISE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"self-harm|suicide|kill myself|end my life", re.I), "[SAFETY_CRITICAL_DISTRESS]"),
    (re.compile(r"\b(?:intimate|sexual)\s+details?\b", re.I), "[SENSITIVE_INTIMATE_CONTENT]"),
]

_OUTPUT_PATTERNS = _SECRET_PATTERNS + _PROMPT_NOISE_PATTERNS
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|auth[_-]?token|authorization|bearer|cookie|password|passwd|secret|credential|dsn)"
)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def redact_text(value: Any) -> str:
    """Redact text before it is printed or injected into an agent prompt."""
    text = _as_text(value)
    for pattern, replacement in _OUTPUT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_embedding_input(value: Any) -> str:
    """Redact infrastructure and secrets before text leaves for embedding."""
    text = _as_text(value)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_obj(value: Any) -> Any:
    """Recursively redact sensitive keys and string values."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = redact_text(str(key))
            if _SENSITIVE_KEY_RE.search(str(key)):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_obj(item)
        return redacted
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_obj(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
