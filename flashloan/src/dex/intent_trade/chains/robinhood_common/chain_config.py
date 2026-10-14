"""Robinhood mainnet/testnet anchor configuration.

The two networks share the same candidate/observation/loop semantics.  Only
chain identity, market id, wrapped native and the exact five anchor addresses
differ here; page builders and chain modules should import this module instead
of re-declaring the anchor set.
"""

from __future__ import annotations

import json
from typing import Any


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_STOCK_REGISTRY_CACHE: dict[str, dict[str, Any]] = {}

ROBINHOOD_NETWORK = "robinhood"
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_MARKET_ID = "dex-arbitrage-4663-uniswap-v4"

ROBINHOOD_TESTNET_NETWORK = "robinhood-testnet"
ROBINHOOD_TESTNET_CHAIN_ID = 46630
ROBINHOOD_TESTNET_MARKET_ID = "dex-arbitrage-46630-uniswap-v4"

MAINNET_ANCHOR_SYMBOLS = ("ETH", "USDG", "USDE", "TZ", "WETH")
MAINNET_ANCHOR_ADDRESS_BY_SYMBOL = {
    "ETH": ZERO_ADDRESS,
    "WETH": "0x0bd7d308f8e1639fab988df18a8011f41eacad73",
    "USDG": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
    "USDE": "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",
    "TZ": "0xa8cf7c51eee671646ba440ffa180f970902fd2fb",
}

TESTNET_ANCHOR_SYMBOLS = ("ETH", "WETH", "TZ", "USDE", "USDG")
TESTNET_ANCHOR_ADDRESS_BY_SYMBOL = {
    "ETH": ZERO_ADDRESS,
    "WETH": "0x7943e237c7f95da44e0301572d358911207852fa",
    "TZ": "0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e",
    "USDE": "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",
    "USDG": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
}

def _stock_anchor_rows_from_db(
    network: str,
    *,
    database_url: str | None = None,
) -> dict[str, str]:
    """Read official stock anchors from SQLite ``chain_tokens`` metadata."""
    normalized = str(network or "").strip().lower()
    chain_id = (
        ROBINHOOD_TESTNET_CHAIN_ID
        if normalized == ROBINHOOD_TESTNET_NETWORK
        else ROBINHOOD_CHAIN_ID
    )
    try:
        from core.db.database_path import absolute_database_path
        from core.db.storage_chain_core import load_chain_tokens

        rows = load_chain_tokens(
            str(absolute_database_path(database_url)),
            network=normalized or ROBINHOOD_NETWORK,
            chain_id=chain_id,
        )
    except Exception:
        return {}
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        address = str(row.get("token_address") or "").strip().lower()
        symbol = str(row.get("token_symbol") or "").strip().upper()
        if not address or not symbol:
            continue
        metadata = row.get("token_metadata_json") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("category") or "").strip().lower() == "robinhood_stock":
            result[symbol] = address
    return result


def configured_anchor_address_by_symbol(
    network: str,
    *,
    stock_registry: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> dict[str, str]:
    """Return the exact configured anchor address map for a Robinhood network.

    Base anchors plus official stock tokens are both first-class anchors.
    """
    result = (
        dict(TESTNET_ANCHOR_ADDRESS_BY_SYMBOL)
        if str(network or "").strip().lower() == ROBINHOOD_TESTNET_NETWORK
        else dict(MAINNET_ANCHOR_ADDRESS_BY_SYMBOL)
    )
    for symbol, address in configured_stock_anchor_address_by_symbol(
        network,
        stock_registry=stock_registry,
        database_url=database_url,
    ).items():
        result[symbol] = address
    return result


def all_anchor_symbols(
    network: str,
    *,
    stock_registry: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> tuple[str, ...]:
    """Return every configured anchor symbol, base first then official stocks."""
    base = (
        TESTNET_ANCHOR_SYMBOLS
        if str(network or "").strip().lower() == ROBINHOOD_TESTNET_NETWORK
        else MAINNET_ANCHOR_SYMBOLS
    )
    stock_symbols = sorted(
        configured_stock_anchor_address_by_symbol(
            network,
            stock_registry=stock_registry,
            database_url=database_url,
        )
    )
    return tuple(dict.fromkeys([*base, *stock_symbols]))


def all_anchor_addresses_by_symbol(
    network: str,
    *,
    stock_registry: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> dict[str, str]:
    """Return base and stock anchor addresses in one map."""
    base = (
        TESTNET_ANCHOR_ADDRESS_BY_SYMBOL
        if str(network or "").strip().lower() == ROBINHOOD_TESTNET_NETWORK
        else MAINNET_ANCHOR_ADDRESS_BY_SYMBOL
    )
    result = dict(base)
    result.update(configured_stock_anchor_address_by_symbol(
        network,
        stock_registry=stock_registry,
        database_url=database_url,
    ))
    return result


def configured_stock_anchor_address_by_symbol(
    network: str,
    *,
    stock_registry: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> dict[str, str]:
    """Return official stock symbols that are treated as borrow anchors.

    Stock tokens are first-class anchors on Robinhood.  The full official
    registry is the configured set; per-address pool membership is enforced
    later by the import/candidate builder.
    """
    normalized = str(network or "").strip().lower()
    if stock_registry is not None:
        items = stock_registry.get("tokens") if isinstance(stock_registry.get("tokens"), dict) else {}
    elif database_url is not None:
        return _stock_anchor_rows_from_db(
            normalized,
            database_url=database_url,
        )
    else:
        cache_key = normalized or ROBINHOOD_NETWORK
        if cache_key not in _STOCK_REGISTRY_CACHE:
            from dex.intent_trade.chains.robinhood_common.stock_token_registry import (
                load_stock_token_registry,
            )

            _STOCK_REGISTRY_CACHE[cache_key] = load_stock_token_registry(
                network=cache_key,
                chain_id=(
                    ROBINHOOD_TESTNET_CHAIN_ID
                    if cache_key == ROBINHOOD_TESTNET_NETWORK
                    else ROBINHOOD_CHAIN_ID
                ),
            )
        items = (_STOCK_REGISTRY_CACHE[cache_key].get("tokens") or {})
    result: dict[str, str] = {}
    for item in items.values():
        if not isinstance(item, dict):
            continue
        address = str(item.get("address") or "").strip().lower()
        symbol = str(item.get("symbol") or "").strip().upper()
        if address and symbol:
            result[symbol] = address
    return result


__all__ = [
    "MAINNET_ANCHOR_ADDRESS_BY_SYMBOL",
    "MAINNET_ANCHOR_SYMBOLS",
    "ROBINHOOD_CHAIN_ID",
    "ROBINHOOD_MARKET_ID",
    "ROBINHOOD_NETWORK",
    "ROBINHOOD_TESTNET_CHAIN_ID",
    "ROBINHOOD_TESTNET_MARKET_ID",
    "ROBINHOOD_TESTNET_NETWORK",
    "TESTNET_ANCHOR_ADDRESS_BY_SYMBOL",
    "TESTNET_ANCHOR_SYMBOLS",
    "ZERO_ADDRESS",
    "all_anchor_addresses_by_symbol",
    "all_anchor_symbols",
    "configured_anchor_address_by_symbol",
    "configured_stock_anchor_address_by_symbol",
]
