"""Transient RPC retry helpers shared by mainnet and testnet V4 quote adapters."""

from __future__ import annotations

from typing import Iterable


_RPC_TRANSIENT_MARKERS = (
    "network is busy",
    "please try again",
    "too many requests",
    "rate limit",
    "rate limited",
    "request rate",
    "429",
    "service unavailable",
    "temporarily unavailable",
    "server busy",
    "busy, please",
    "overloaded",
    "bad gateway",
    "unexpected_eof",
    "connection reset",
    "connection aborted",
    "remote disconnected",
    "read timeout",
    "econnreset",
    "socket hang up",
    "headers timeout error",
    "connect timeout error",
)


def is_transient_rpc_error(exc: Exception) -> bool:
    """Return whether an RPC quote error can reasonably be retried."""
    message = str(exc or "").strip().lower()
    if any(marker in message for marker in _RPC_TRANSIENT_MARKERS):
        return True
    if "rpc" in message and (
        "unavailable" in message
        or "timed out" in message
        or "timeout" in message
        or "failed" in message
    ):
        return True
    return False


def ordered_rpc_urls(current: str | None, configured: Iterable[str]) -> tuple[str, ...]:
    """Order failover endpoints with the active/cached endpoint first."""
    result: list[str] = []
    for url in (current, *tuple(configured)):
        value = str(url or "").strip()
        if value and value not in result:
            result.append(value)
    return tuple(result)


def copy_rpc_exception_metadata(source: Exception, target: Exception) -> Exception:
    """Copy RPC failover audit fields onto an outer wrapper exception."""
    for name in ("attemptedRpcUrls", "rpcFailoverCount", "rpcBusy"):
        if hasattr(source, name):
            setattr(target, name, getattr(source, name))
    return target


__all__ = [
    "copy_rpc_exception_metadata",
    "is_transient_rpc_error",
    "ordered_rpc_urls",
]
