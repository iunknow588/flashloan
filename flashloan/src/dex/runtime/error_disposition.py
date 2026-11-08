"""Resolve structured dispositions for broadcast/review error strings.

The strategy matrix lives in ``plan/错误处置策略_20261029.md`` and is encoded
in ``core.error_registry``.  This module maps raw runtime error text to a
stable ``errorKey/errorCode/disposition`` triple, delegating revert decoding to
``revert_parser`` and adding transport/submission text patterns that are not
contract reverts.
"""

from __future__ import annotations

import os
import re
from typing import Any

from core.error_registry import (
    DISPOSITION_BY_NAME,
    ERROR_REGISTRY,
    LEGACY_ERROR_KEYS,
    resolve_disposition,
    resolve_error_definition,
)
from bot.execution.revert_parser import parse_revert_reason


_EXTRA_ERROR_TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("already_known", re.compile(r"already\s+known", re.IGNORECASE)),
    (
        "broadcast_not_accepted",
        re.compile(r"(?:insufficient\s+funds|intrinsic\s+gas\s+too\s+low|not\s+accepted|rejected)", re.IGNORECASE),
    ),
    (
        "broadcast_transport_timeout",
        re.compile(
            r"(?:timeout|timed\s+out|connection\s+refused|connection\s+reset|reset\s+by\s+peer|"
            r"econnreset|429|502|503|504)",
            re.IGNORECASE,
        ),
    ),
    ("execution_queued", re.compile(r"(?:queued|deferred)", re.IGNORECASE)),
)


def resubmit_same_raw_enabled() -> bool:
    value = str(os.getenv("DEX_RESUBMIT_SAME_RAW_ENABLED", "0") or "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _normalized_name_from_text(error_text: str) -> str | None:
    text = str(error_text or "").strip()
    if not text:
        return None
    for name, pattern in _EXTRA_ERROR_TEXT_PATTERNS:
        if pattern.search(text):
            return name
    return None


def resolve_broadcast_error(
    error_text: str | None,
    *,
    error_key: str | None = None,
) -> dict[str, Any]:
    """Return a stable structured error payload for a failure message."""
    text = str(error_text or "").strip()
    parsed = parse_revert_reason(text)
    category = str(parsed.get("category") or "unknown").strip().lower()
    parsed_key = str(parsed.get("error_key") or "").strip()
    if error_key and error_key in ERROR_REGISTRY:
        definition = ERROR_REGISTRY[error_key]
        disposition = resolve_disposition(error_key)
        return {
            "errorKey": definition.key,
            "errorCode": int(definition.code),
            "errorName": str(error_key).lower(),
            "disposition": disposition,
            "raw": text,
            "confidence": "high",
        }
    if parsed_key and category not in {"", "unknown"}:
        definition = resolve_error_definition(parsed_key)
        # Category names carry finer strategy semantics than the generic
        # registry key (e.g. slippage is requote, not design-blocked).
        disposition = resolve_disposition(category)
        return {
            "errorKey": definition.key,
            "errorCode": int(definition.code),
            "errorName": category,
            "disposition": disposition,
            "raw": text,
            "confidence": str(parsed.get("confidence") or "low"),
        }
    normalized_name = _normalized_name_from_text(text)
    if normalized_name:
        legacy_key = LEGACY_ERROR_KEYS.get(normalized_name)
        definition = (
            resolve_error_definition(legacy_key)
            if legacy_key
            else resolve_error_definition("DEX.INTERNAL")
        )
        return {
            "errorKey": definition.key,
            "errorCode": int(definition.code),
            "errorName": normalized_name,
            "disposition": resolve_disposition(normalized_name),
            "raw": text,
            "confidence": "high",
        }
    direct_name = text.lower()
    if direct_name in LEGACY_ERROR_KEYS or direct_name in DISPOSITION_BY_NAME:
        legacy_key = LEGACY_ERROR_KEYS.get(direct_name)
        definition = (
            resolve_error_definition(legacy_key)
            if legacy_key
            else resolve_error_definition("BOT.INTERNAL")
        )
        return {
            "errorKey": definition.key,
            "errorCode": int(definition.code),
            "errorName": direct_name,
            "disposition": resolve_disposition(direct_name),
            "raw": text,
            "confidence": "high",
        }
    definition = resolve_error_definition("BOT.INTERNAL")
    return {
        "errorKey": definition.key,
        "errorCode": int(definition.code),
        "errorName": "unknown",
        "disposition": resolve_disposition("unknown"),
        "raw": text,
        "confidence": "low",
    }


def error_fingerprint(error: dict[str, Any]) -> str:
    name = str(error.get("errorName") or error.get("errorKey") or "unknown").strip().lower()
    return name or "unknown"


__all__ = [
    "error_fingerprint",
    "resubmit_same_raw_enabled",
    "resolve_broadcast_error",
]
