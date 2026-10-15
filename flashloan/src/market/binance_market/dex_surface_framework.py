"""Framework-level DEX surface configuration and token metadata helpers."""

from __future__ import annotations

import os
from typing import Any

from dex.intent_trade.chains.robinhood.shared import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_TESTNET_CHAIN_ID,
)
from market.binance_market.pool_candidates import dex_pool_candidates_payload
from market.binance_market.dex_surface_types import _norm_symbol, _token_address
from market.binance_market.dex_surface_v4 import (
    _live_v4_token_decimals as _default_live_v4_token_decimals,
    _pool_manager_address as _default_pool_manager_address,
    _v4_live_token_metadata_enabled as _default_v4_live_token_metadata_enabled,
    _v4_token_metadata_cache as _default_v4_token_metadata_cache,
)

_ROBINHOOD_CHAIN_IDS = {ROBINHOOD_CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID}


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _v4_live_quote_enabled(chain_id: int | None = None) -> bool:
    raw = os.getenv("DEX_V4_LIVE_QUOTE_ENABLED")
    if raw is not None:
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    # Robinhood mainnet/testnet quote/routing review is expected to use live
    # Quoter/eth_call evidence, not just static reference prices.
    return int(chain_id or 0) in _ROBINHOOD_CHAIN_IDS


def _robinhood_static_scope_protocols(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    use_static_observation: bool = False,
) -> list[str]:
    """Static Robinhood Testnet overview/routes use the compact all-scope snapshot.

    The DEX overview's closed-loop routes are the source of truth for quote
    routing. Protocol filters still describe the requested market, but they must
    not cause the UI to show generated route combinations that did not come from
    that closed-loop observation set.
    """
    # Keep the requested protocol scope when a protocol-specific snapshot is
    # available. Returning an empty scope here silently selected the `all`
    # snapshot and could detach V4 quotes from the V4 closed-loop overview.
    return list(protocols or [])


def _v4_execution_size_probe_enabled() -> bool:
    return _env_flag("DEX_V4_EXECUTION_SIZE_PROBE_ENABLED", default=False)


def _v4_live_token_metadata_enabled() -> bool:
    return _env_flag("DEX_V4_LIVE_TOKEN_METADATA_ENABLED", default=False)


def _v4_pool_token_metadata(
    pool: dict[str, Any],
    index: int,
    *,
    chain_id: int | None = None,
    token_metadata_cache=_default_v4_token_metadata_cache,
    live_token_metadata_enabled=_default_v4_live_token_metadata_enabled,
    live_v4_token_decimals=_default_live_v4_token_decimals,
    pool_manager_address=_default_pool_manager_address,
) -> dict[str, Any]:
    address = _token_address(pool.get(f"token{index}"))
    symbol = _norm_symbol(pool.get(f"token{index}Symbol"))
    decimals_value = pool.get(f"token{index}Decimals") or pool.get(f"token{index}_decimals")
    try:
        decimals = int(decimals_value) if decimals_value not in (None, "") else None
    except (TypeError, ValueError):
        decimals = None
    if address == "0x0000000000000000000000000000000000000000":
        return {
            "address": address,
            "symbol": symbol or "ETH",
            "decimals": 18,
            "metadataOk": True,
            "source": "native_currency",
        }
    if address:
        cached = token_metadata_cache(pool_manager_address(pool, chain_id)).get(address.lower())
        if cached:
            symbol = symbol or _norm_symbol(cached.get("symbol"))
            if decimals is None:
                try:
                    cached_decimals = cached.get("decimals")
                    decimals = int(cached_decimals) if cached_decimals not in (None, "") else None
                except (TypeError, ValueError):
                    decimals = None
            if decimals is not None:
                return {
                    "address": address,
                    "symbol": symbol or address[:8].upper(),
                    "decimals": decimals,
                    "metadataOk": bool(symbol and decimals is not None),
                    "source": "sqlite_db",
                }
        if live_token_metadata_enabled():
            live_decimals = live_v4_token_decimals(address, chain_id=chain_id)
            if live_decimals is not None:
                if not symbol:
                    symbol = address[:8].upper()
                return {
                    "address": address,
                    "symbol": symbol,
                    "decimals": live_decimals,
                    "metadataOk": bool(symbol and live_decimals is not None),
                    "source": "chain_live_erc20_decimals",
                }
    if not symbol and address:
        symbol = address[:8].upper()
    return {
        "address": address,
        "symbol": symbol,
        "decimals": decimals,
        "metadataOk": bool(symbol and decimals is not None),
        "source": "pool_index",
    }


def _candidate_payload(chain_id: int | None, protocols: list[str] | None = None) -> dict[str, Any]:
    return dex_pool_candidates_payload(chain_id=chain_id, protocols=protocols or [])


__all__ = [
    "_candidate_payload",
    "_env_flag",
    "_robinhood_static_scope_protocols",
    "_v4_execution_size_probe_enabled",
    "_v4_live_quote_enabled",
    "_v4_live_token_metadata_enabled",
    "_v4_pool_token_metadata",
]
