"""V4 pool startup-scope mechanism for Robinhood chains.

The pool index object does not carry a per-row enable flag. Instead the
initialization mode decides which token universe the pool bootstrap reads.
``full`` preserves the existing behavior and ``core4`` limits the pool set to
USDG/USDE/ETH/WETH. Future modes can be added to the registry below without
changing the call shape used by refresh and maintenance entry points.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from dex.intent_trade.chains.robinhood_common.chain_config import (
    MAINNET_ANCHOR_ADDRESS_BY_SYMBOL,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_NETWORK,
    TESTNET_ANCHOR_ADDRESS_BY_SYMBOL,
)


POOL_SCOPE_FULL = "full"
POOL_SCOPE_CORE4 = "core4"
POOL_SCOPE_ENV = "ROBINHOOD_V4_POOL_SCOPE"

CORE4_SYMBOLS = ("ETH", "WETH", "USDG", "USDE")

# Placeholder for modes that are not implemented yet. Keeping them in a
# dedicated registry makes the extension point visible without allowing an
# unsupported mode to silently behave like the full scope.
RESERVED_POOL_SCOPES = {
    "all_except_tz",
}

_POOL_SCOPE_SYMBOLS: dict[str, tuple[str, ...] | None] = {
    POOL_SCOPE_FULL: None,
    POOL_SCOPE_CORE4: CORE4_SYMBOLS,
}


def normalize_pool_scope(value: object = None) -> str:
    """Return the canonical startup mode, applying the env var when omitted."""
    raw = str(
        value if value is not None else os.getenv(POOL_SCOPE_ENV, POOL_SCOPE_FULL)
    ).strip().lower().replace("-", "_")
    if raw in _POOL_SCOPE_SYMBOLS:
        return raw
    if raw in RESERVED_POOL_SCOPES:
        raise ValueError(
            f"pool_scope {raw!r} is reserved but not implemented; "
            f"implemented scopes are {sorted(_POOL_SCOPE_SYMBOLS)!r}"
        )
    raise ValueError(
        f"unsupported pool_scope {raw!r}; "
        f"implemented scopes are {sorted(_POOL_SCOPE_SYMBOLS)!r}"
    )


def pool_scope_token_symbols(pool_scope: object = None) -> tuple[str, ...] | None:
    """Return token symbols selected by the startup mode, or None for full."""
    mode = normalize_pool_scope(pool_scope)
    return _POOL_SCOPE_SYMBOLS[mode]


def pool_scope_token_addresses(
    pool_scope: object = None,
    *,
    network: str = ROBINHOOD_NETWORK,
) -> set[str] | None:
    """Return lower-case token addresses for the selected startup mode."""
    symbols = pool_scope_token_symbols(pool_scope)
    if symbols is None:
        return None
    normalized = str(network or "").strip().lower()
    if normalized == ROBINHOOD_TESTNET_NETWORK:
        symbol_map = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL
    else:
        symbol_map = MAINNET_ANCHOR_ADDRESS_BY_SYMBOL
    addresses = {
        str(symbol_map.get(symbol) or "").strip().lower()
        for symbol in symbols
    }
    return {address for address in addresses if address}


def pool_scope_summary(
    pool_scope: object = None,
    *,
    network: str = ROBINHOOD_NETWORK,
) -> dict[str, Any]:
    """Return a small JSON-friendly description for evidence and reports."""
    mode = normalize_pool_scope(pool_scope)
    symbols = pool_scope_token_symbols(mode)
    addresses = pool_scope_token_addresses(mode, network=network)
    return {
        "mode": mode,
        "implemented": True,
        "tokenSymbols": list(symbols) if symbols else None,
        "tokenAddresses": sorted(addresses) if addresses else [],
        "reservedScopes": sorted(RESERVED_POOL_SCOPES),
    }


__all__ = [
    "CORE4_SYMBOLS",
    "POOL_SCOPE_CORE4",
    "POOL_SCOPE_ENV",
    "POOL_SCOPE_FULL",
    "RESERVED_POOL_SCOPES",
    "normalize_pool_scope",
    "pool_scope_summary",
    "pool_scope_token_addresses",
    "pool_scope_token_symbols",
]
