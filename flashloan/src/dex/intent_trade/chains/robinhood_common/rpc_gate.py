"""Central live-quote RPC gate for Robinhood V4 automatic paths.

The quote and routing page requires live on-chain quoting, so there is no
per-chain live-quote switch.  Independent controls keep one responsibility
per knob:

- ``DEX_V4_REBUILD_RPC_MODE=offline|online`` controls SQLite rebuild tools.
- ``DEX_V4_EDGE_QUOTE_MODE=off|reference|live|auto`` controls edge quotes.

Network disconnection is a dynamic runtime state, not a static parameter:
RPC probe/connection failures call ``mark_v4_rpc_offline`` and a later
successful probe calls ``clear_v4_rpc_offline``.  The quote/routing page stays
fail-closed while a link is offline.

Explicit maintenance/refresh tools may bypass this gate by invoking their own
RPC entry points directly.
"""

from __future__ import annotations

import os

from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_TESTNET_CHAIN_ID,
)

_ENABLED_VALUES = {"1", "true", "yes", "on"}
_ROBINHOOD_CHAIN_IDS = {ROBINHOOD_CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID}

# Runtime status emitted when a Robinhood V4 chain link is dynamically offline.
# Kept in one place so evidence, APIs, UI maps and tests use the same string.
RPC_UNAVAILABLE_STATUS = "rpc_unavailable"
# Historical evidence and older UI payloads used this string. It is only a
# read-compatible alias; production code must not emit it as a live status.
LEGACY_LIVE_QUOTE_DISABLED_STATUS = "live_quote_disabled"

_OFFLINE_REASONS: dict[int, str] = {}
_REBUILD_RPC_MODE_ENV = "DEX_V4_REBUILD_RPC_MODE"
_EDGE_QUOTE_MODE_ENV = "DEX_V4_EDGE_QUOTE_MODE"


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in _ENABLED_VALUES


def mark_v4_rpc_offline(chain_id: int, *, reason: str) -> None:
    """Record a network-level RPC failure as dynamic offline state."""
    _OFFLINE_REASONS[int(chain_id)] = str(reason or "rpc_unreachable")


def clear_v4_rpc_offline(chain_id: int) -> None:
    """Clear dynamic offline state after a successful RPC probe."""
    _OFFLINE_REASONS.pop(int(chain_id), None)


def v4_rpc_offline_reason(chain_id: int | None = None) -> str | None:
    """Return the active dynamic offline reason, if any."""
    chain_id = int(chain_id or 0)
    if chain_id == 0:
        if not _OFFLINE_REASONS:
            return None
        return next(iter(_OFFLINE_REASONS.values()))
    return _OFFLINE_REASONS.get(chain_id)


def dex_rpc_offline(chain_id: int | None = None) -> bool:
    """Return True when the chain link is dynamically offline."""
    if chain_id is None:
        return bool(_OFFLINE_REASONS)
    return int(chain_id) in _OFFLINE_REASONS


def v4_rebuild_rpc_mode() -> str:
    """Return the SQLite rebuild RPC scope: ``offline`` (default) or ``online``."""
    normalized = str(os.getenv(_REBUILD_RPC_MODE_ENV) or "offline").strip().lower()
    return normalized if normalized in {"offline", "online"} else "offline"


def require_v4_rebuild_online(*, operation: str = "V4 live rebuild") -> None:
    if v4_rebuild_rpc_mode() != "online":
        raise RuntimeError(
            f"{operation} disabled in offline rebuild mode: "
            f"set {_REBUILD_RPC_MODE_ENV}=online to allow RPC."
        )


def v4_edge_quote_mode() -> str:
    """Return ``off``, ``reference``, ``live`` or ``auto`` for V4 edge quotes."""
    normalized = str(os.getenv(_EDGE_QUOTE_MODE_ENV) or "auto").strip().lower()
    if normalized in {"off", "reference", "live", "auto"}:
        return normalized
    return "auto"


def v4_live_quote_enabled(chain_id: int | None = None) -> bool:
    chain_id = int(chain_id or 0)
    if dex_rpc_offline(chain_id=chain_id):
        return False
    return chain_id in _ROBINHOOD_CHAIN_IDS


def require_v4_live_quote(chain_id: int | None = None, *, operation: str = "V4 live quote") -> None:
    if not v4_live_quote_enabled(chain_id=chain_id):
        reason = v4_rpc_offline_reason(chain_id=chain_id) or "unsupported_chain"
        raise RuntimeError(
            f"{operation} disabled for chain_id={chain_id}: "
            f"dynamic RPC state is offline ({reason})"
        )


__all__ = [
    "LEGACY_LIVE_QUOTE_DISABLED_STATUS",
    "RPC_UNAVAILABLE_STATUS",
    "clear_v4_rpc_offline",
    "dex_rpc_offline",
    "mark_v4_rpc_offline",
    "require_v4_live_quote",
    "require_v4_rebuild_online",
    "v4_edge_quote_mode",
    "v4_live_quote_enabled",
    "v4_rpc_offline_reason",
    "v4_rebuild_rpc_mode",
]
