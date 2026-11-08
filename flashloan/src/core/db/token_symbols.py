"""Shared symbol-quality helpers for database-first DEX builders."""

from __future__ import annotations

import re


_UNRESOLVED_PLACEHOLDER_SYMBOL_RE = re.compile(r"0X[0-9A-F]{6,8}")
_UNRESOLVED_PREFIX_RE = re.compile(r"UNRESOLVED:", re.IGNORECASE)


def is_unresolved_placeholder_symbol(symbol: object) -> bool:
    """Return whether a symbol is the factory-deployed ``0X`` placeholder.

    Factory-deployed tokens report ``0X`` plus eight hex digits as their ERC20
    symbol only when official allowlist metadata has not been resolved.  The
    SQLite token registry can also retain a shortened six-digit form for the
    same unresolved token.  The rule is deliberately generic and has no
    TA/TB/TZ/ETH special cases.
    """
    normalized = str(symbol or "").strip().upper()
    return bool(_UNRESOLVED_PREFIX_RE.match(normalized)) or bool(
        _UNRESOLVED_PLACEHOLDER_SYMBOL_RE.fullmatch(normalized)
    )


__all__ = ["is_unresolved_placeholder_symbol"]
