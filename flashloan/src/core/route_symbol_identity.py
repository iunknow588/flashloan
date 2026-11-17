"""Chain-owned route symbol/address identity helpers.

This module is deliberately owned by ``core`` because route identity is a
storage and validation concern.  Market and review code may consume it, but
core must not depend on market implementations for canonical token identity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from web3 import Web3

from core.chain_data_registry import chain_data_by_chain_id, chain_data_file_path
from core.db.chain_database_context import chain_database_path
from core.db.database_path import absolute_database_path
from core.db.storage_chain_core import load_chain_tokens
from core.db.token_symbols import is_unresolved_placeholder_symbol


_ROBINHOOD_MAINNET_CHAIN_ID = 4663
_ROBINHOOD_TESTNET_CHAIN_ID = 46630
_ROBINHOOD_TESTNET_NETWORK = "robinhood-testnet"
# Cache holds three parts per chain:
#   * resolved symbols -> address (authoritative identity)
#   * placeholder aliases -> address (forward-only repair map)
#   * ambiguous symbols (the same symbol claimed by two addresses)
_SYMBOL_ADDRESS_CACHE: dict[int, tuple[dict[str, str], dict[str, str], set[str]]] = {}


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
        candidate = Path(__file__).resolve().parents[2] / "chains" / network / "v4_token_allowlist.json"
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


def _placeholder_symbol_aliases(address: str) -> tuple[str, ...]:
    """Return the factory placeholder spellings derived from one address.

    Factory-deployed tokens report ``symbol()`` as ``0X`` plus the leading
    hex digits of their own address.  The registry also retains a shortened
    six-digit spelling for the same token.  Both spellings are derived from
    the address, so a stale route symbol can be mapped back to its address
    without a hand-written per-token alias.
    """

    normalized = str(address or "").strip().lower()
    if not normalized.startswith("0x") or len(normalized) != 42:
        return ()
    body = normalized[2:]
    aliases: list[str] = []
    for digits in (8, 6):
        candidate = "0X" + body[:digits].upper()
        if candidate not in aliases:
            aliases.append(candidate)
    return tuple(aliases)


def _checksum_address(address: str) -> str:
    """Return the EIP-55 checksum spelling of one address.

    The static allowlist stores checksummed addresses while the SQLite
    registry stores lowercase ones.  Normalizing the registry to the same
    checksummed form keeps a route identity stable no matter which
    chain-owned source supplied the address.
    """

    normalized = str(address or "").strip()
    if not normalized.startswith("0x") or len(normalized) != 42:
        return normalized
    try:
        return Web3.to_checksum_address(normalized)
    except Exception:
        return normalized


def _load_stock_tokens_from_registry(
    network: str,
    chain_id: int,
) -> tuple[dict[str, str], dict[str, str], set[str]]:
    """Load authoritative stock-token identity from the local registry.

    The registry is the observed chain source: the token metadata resolver
    repairs factory placeholder symbols there, so a route that still carries a
    stale ``0X........`` placeholder can be mapped back to the real symbol and
    address without reintroducing a duplicate alias into the static allowlist.
    """
    mapping: dict[str, str] = {}
    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()
    try:
        rows = load_chain_tokens(
            str(chain_database_path(chain_id, network)),
            network=network,
            chain_id=chain_id,
        )
    except (OSError, TypeError, ValueError):
        return mapping, aliases, ambiguous
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
        address = _checksum_address(str(row.get("token_address") or "").strip())
        if not symbol or not address:
            continue
        if is_unresolved_placeholder_symbol(symbol):
            # Unresolved factory placeholder rows keep the address that the
            # placeholder was derived from.  Index the bare ``0X........``
            # spelling as a forward-only alias so a stale route symbol can be
            # mapped back to its address without claiming the authoritative
            # symbol of that address.
            placeholder = symbol.split(":")[-1].strip().upper()
            if placeholder and placeholder not in aliases:
                aliases[placeholder] = address
            continue
        existing = mapping.get(symbol)
        if existing is not None and existing.lower() != address.lower():
            ambiguous.add(symbol)
            mapping.pop(symbol, None)
        elif existing is None:
            mapping[symbol] = address
    for symbol, address in list(mapping.items()):
        for alias in _placeholder_symbol_aliases(address):
            if alias in mapping or alias in aliases or alias in ambiguous:
                continue
            aliases[alias] = address
    return mapping, aliases, ambiguous


def _load_chain_symbol_addresses(
    chain_id: int | None,
) -> tuple[dict[str, str], dict[str, str], set[str]]:
    """Return resolved symbols, placeholder aliases, and ambiguous symbols.

    ``resolved`` maps an authoritative symbol to its address.  ``aliases``
    maps a stale factory placeholder spelling to the same address so a route
    observed before token metadata repair still resolves.  Aliases never take
    part in the reverse address lookup, so one address keeps exactly one
    authoritative symbol.
    """
    key = int(chain_id or 0)
    cached = _SYMBOL_ADDRESS_CACHE.get(key)
    if cached is not None:
        return cached
    resolved, ambiguous = _load_json_tokens(_chain_token_allowlist_path(key))
    aliases: dict[str, str] = {}
    stock_network = {
        _ROBINHOOD_MAINNET_CHAIN_ID: "robinhood",
        _ROBINHOOD_TESTNET_CHAIN_ID: _ROBINHOOD_TESTNET_NETWORK,
    }.get(key)
    if stock_network:
        stock_resolved, stock_aliases, stock_ambiguous = _load_stock_tokens_from_registry(
            stock_network,
            key,
        )
        ambiguous.update(stock_ambiguous)
        for symbol, address in stock_resolved.items():
            existing = resolved.get(symbol)
            if existing is not None and existing.lower() != address.lower():
                ambiguous.add(symbol)
                resolved.pop(symbol, None)
            elif existing is None:
                resolved[symbol] = address
        for alias, address in stock_aliases.items():
            if alias not in resolved and alias not in ambiguous:
                aliases[alias] = address
    _SYMBOL_ADDRESS_CACHE[key] = (resolved, aliases, ambiguous)
    return resolved, aliases, ambiguous


def official_route_addresses(route_symbols: list[str], *, chain_id: int | None = None) -> list[str]:
    """Resolve a complete route through chain-owned symbol/address data.

    A stale factory placeholder spelling is accepted when the chain-owned
    registry can map it to a real address, so a route observed before token
    metadata repair still resolves end to end.
    """
    if chain_id is None:
        return []
    symbol_addresses, alias_addresses, ambiguous_symbols = _load_chain_symbol_addresses(
        int(chain_id)
    )
    normalized = [str(symbol or "").strip().upper() for symbol in route_symbols]
    if not normalized or any(symbol in ambiguous_symbols for symbol in normalized):
        return []
    resolved: list[str] = []
    for symbol in normalized:
        address = symbol_addresses.get(symbol) or alias_addresses.get(symbol)
        if not address:
            return []
        resolved.append(address)
    return resolved


def _trusted_address_symbol_map(chain_id: int | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    symbol_addresses, _alias_addresses, ambiguous_symbols = _load_chain_symbol_addresses(
        chain_id
    )
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
