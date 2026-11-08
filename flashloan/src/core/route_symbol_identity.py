"""Chain-owned route symbol/address identity helpers.

This module is deliberately owned by ``core`` because route identity is a
storage and validation concern.  Market and review code may consume it, but
core must not depend on market implementations for canonical token identity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id, chain_data_file_path
from core.db.database_path import absolute_database_path
from core.db.storage_chain_core import load_chain_tokens
from core.db.token_symbols import is_unresolved_placeholder_symbol


_ROBINHOOD_MAINNET_CHAIN_ID = 4663
_ROBINHOOD_TESTNET_CHAIN_ID = 46630
_ROBINHOOD_TESTNET_NETWORK = "robinhood-testnet"
_SYMBOL_ADDRESS_CACHE: dict[int, tuple[dict[str, str], set[str]]] = {}


def _chain_token_allowlist_path(chain_id: int | None) -> Path | None:
    row = chain_data_by_chain_id(chain_id) if chain_id is not None else None
    if not row:
        return None
    data_files = row.get("data_files") if isinstance(row.get("data_files"), dict) else {}
    relative = str(data_files.get("v4_token_allowlist") or "").strip()
    if relative:
        return Path(__file__).resolve().parents[1] / relative
    network = str(row.get("network") or "").strip()
    if network:
        candidate = Path(__file__).resolve().parents[1] / "chains" / network / "v4_token_allowlist.json"
        if candidate.exists():
            return candidate
    return None


def _load_json_tokens(path: Path | None) -> tuple[dict[str, str], set[str]]:
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    if path is None:
        return mapping, ambiguous
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return mapping, ambiguous
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    for item in (tokens or {}).values() if isinstance(tokens, dict) else []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        address = str(item.get("address") or "").strip()
        if not symbol or not address:
            continue
        existing = mapping.get(symbol)
        if existing is not None and existing.lower() != address.lower():
            ambiguous.add(symbol)
            mapping.pop(symbol, None)
        elif existing is None:
            mapping[symbol] = address
    return mapping, ambiguous


def _load_mainnet_stock_tokens() -> tuple[dict[str, str], set[str]]:
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    try:
        rows = load_chain_tokens(
            str(absolute_database_path()),
            network="robinhood",
            chain_id=_ROBINHOOD_MAINNET_CHAIN_ID,
        )
    except (OSError, TypeError, ValueError):
        return mapping, ambiguous
    for row in rows:
        if not isinstance(row, dict):
            continue
        metadata = row.get("token_metadata_json") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("category") or "").strip().lower() != "robinhood_stock":
            continue
        symbol = str(row.get("token_symbol") or "").strip().upper()
        address = str(row.get("token_address") or "").strip()
        if not symbol or not address:
            continue
        existing = mapping.get(symbol)
        if existing is not None and existing.lower() != address.lower():
            ambiguous.add(symbol)
            mapping.pop(symbol, None)
        elif existing is None:
            mapping[symbol] = address
    return mapping, ambiguous


def _load_chain_symbol_addresses(chain_id: int | None) -> tuple[dict[str, str], set[str]]:
    key = int(chain_id or 0)
    cached = _SYMBOL_ADDRESS_CACHE.get(key)
    if cached is not None:
        return cached
    mapping, ambiguous = _load_json_tokens(_chain_token_allowlist_path(key))
    if key == _ROBINHOOD_MAINNET_CHAIN_ID:
        stock_mapping, stock_ambiguous = _load_mainnet_stock_tokens()
        ambiguous.update(stock_ambiguous)
        for symbol, address in stock_mapping.items():
            existing = mapping.get(symbol)
            if existing is not None and existing.lower() != address.lower():
                ambiguous.add(symbol)
                mapping.pop(symbol, None)
            elif existing is None:
                mapping[symbol] = address
    _SYMBOL_ADDRESS_CACHE[key] = (mapping, ambiguous)
    return mapping, ambiguous


def official_route_addresses(route_symbols: list[str], *, chain_id: int | None = None) -> list[str]:
    """Resolve a complete route through chain-owned symbol/address data."""
    if chain_id is None:
        return []
    symbol_addresses, ambiguous_symbols = _load_chain_symbol_addresses(int(chain_id))
    normalized = [str(symbol or "").strip().upper() for symbol in route_symbols]
    if (
        not normalized
        or any(symbol in ambiguous_symbols for symbol in normalized)
        or any(symbol not in symbol_addresses for symbol in normalized)
    ):
        return []
    return [symbol_addresses[symbol] for symbol in normalized]


def _trusted_address_symbol_map(chain_id: int | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    symbol_addresses, ambiguous_symbols = _load_chain_symbol_addresses(chain_id)
    for symbol, address in symbol_addresses.items():
        if symbol in ambiguous_symbols:
            continue
        normalized_address = str(address or "").strip().lower()
        if not normalized_address:
            continue
        existing = mapping.get(normalized_address)
        if existing is None:
            mapping[normalized_address] = symbol
        elif existing != symbol:
            mapping.pop(normalized_address, None)
    return mapping


def trusted_symbol_from_address(address: str | None, chain_id: int | None = None) -> str | None:
    normalized_address = str(address or "").strip().lower()
    if not normalized_address:
        return None
    return _trusted_address_symbol_map(chain_id).get(normalized_address)


def normalize_route_symbols_from_addresses(
    *,
    route_symbols: list[str],
    route_addresses: list[str],
    chain_id: int | None,
    route: dict[str, Any] | None = None,
) -> list[str]:
    """Replace unresolved route symbols using trusted address identity."""
    normalized = [str(symbol or "").strip().upper() for symbol in route_symbols]
    if len(normalized) != len(route_addresses):
        return normalized
    if not any(is_unresolved_placeholder_symbol(symbol) for symbol in normalized):
        return normalized
    mapping = _trusted_address_symbol_map(chain_id)
    return [
        mapping.get(str(address or "").strip().lower(), symbol)
        if is_unresolved_placeholder_symbol(symbol)
        else symbol
        for symbol, address in zip(normalized, route_addresses)
    ]


# Private spelling retained for transitional callers while the public core
# name is adopted by new manager code.
_official_route_addresses = official_route_addresses


__all__ = [
    "_official_route_addresses",
    "normalize_route_symbols_from_addresses",
    "official_route_addresses",
    "trusted_symbol_from_address",
]
