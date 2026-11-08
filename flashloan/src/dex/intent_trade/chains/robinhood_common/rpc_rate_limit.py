"""Process-wide per-endpoint RPC rate limiting.

The free-tier Robinhood testnet endpoint is request-rate limited.  Index
scans, liquidity batches and quoter calls share one cooldown per HTTP endpoint
so bursts from parallel worker threads cannot trip the provider while the
project is still on the free tier.
"""

from __future__ import annotations

import os
import threading
import time


DEFAULT_MIN_INTERVAL_SECONDS = 0.2
_RPC_LAST_STARTED_AT: dict[str, float] = {}
_RPC_RATE_LIMIT_LOCK = threading.Lock()


def dex_rpc_min_interval_seconds() -> float:
    raw = os.getenv("DEX_RPC_MIN_INTERVAL_SECONDS", "")
    if raw:
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return DEFAULT_MIN_INTERVAL_SECONDS
    return DEFAULT_MIN_INTERVAL_SECONDS


def throttle_rpc_call(rpc_url: str | None) -> float:
    """Sleep until the next allowed request slot for the endpoint.

    Returns the number of seconds waited, or ``0.0`` when the interval is
    disabled.  The lock is held across the sleep so concurrent callers are
    serialized per endpoint.
    """
    normalized = str(rpc_url or "").strip()
    if not normalized:
        return 0.0
    interval = dex_rpc_min_interval_seconds()
    if interval <= 0.0:
        return 0.0
    with _RPC_RATE_LIMIT_LOCK:
        now = time.monotonic()
        last = _RPC_LAST_STARTED_AT.get(normalized)
        if last is None:
            _RPC_LAST_STARTED_AT[normalized] = now
            return 0.0
        wait = interval - (now - last)
        if wait > 0.0:
            time.sleep(wait)
            now = time.monotonic()
        _RPC_LAST_STARTED_AT[normalized] = now
        return max(0.0, wait)


def reset_dex_rpc_rate_limit_for_tests() -> None:
    with _RPC_RATE_LIMIT_LOCK:
        _RPC_LAST_STARTED_AT.clear()


__all__ = [
    "DEFAULT_MIN_INTERVAL_SECONDS",
    "dex_rpc_min_interval_seconds",
    "reset_dex_rpc_rate_limit_for_tests",
    "throttle_rpc_call",
]
