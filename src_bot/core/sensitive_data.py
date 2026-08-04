from __future__ import annotations

import os
import re


_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s@]+)@")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret|password|private[_-]?key)=)([^&#\s]+)"
)
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|token|secret|password|private[_-]?key|mnemonic|seed(?:[_-]?phrase)?)\s*=\s*)([^\s,;]+)"
)
_PRIVATE_KEY_LITERAL = re.compile(r"\b0x[a-fA-F0-9]{64}\b")
_PEM_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----", re.DOTALL)


def redact_sensitive_text(value: object) -> str:
    text = str(value)
    for secret in _sensitive_environment_values():
        text = text.replace(secret, "[REDACTED]")
    text = _URL_USERINFO.sub(r"\1[REDACTED]@", text)
    text = _QUERY_SECRET.sub(r"\1[REDACTED]", text)
    text = _ASSIGNMENT_SECRET.sub(r"\1[REDACTED]", text)
    text = _PRIVATE_KEY_LITERAL.sub("[REDACTED_PRIVATE_KEY]", text)
    return _PEM_PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", text)


def _sensitive_environment_values() -> list[str]:
    values: set[str] = set()
    for name, raw_value in os.environ.items():
        if not _is_sensitive_environment_name(name):
            continue
        value = str(raw_value).strip()
        if value:
            values.add(value)
    return sorted(values, key=len, reverse=True)


def _is_sensitive_environment_name(name: str) -> bool:
    normalized = name.upper()
    return (
        normalized.endswith("_RPC_URL")
        or normalized == "DATABASE_URL"
        or any(part in normalized for part in ("PRIVATE_KEY", "MNEMONIC", "SEED", "PASSWORD", "TOKEN", "SECRET", "API_KEY"))
    )
