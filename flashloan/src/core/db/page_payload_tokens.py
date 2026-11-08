"""Shared token lookup helpers for database-first page payloads."""

from __future__ import annotations

import json
from typing import Any

from core.db.database_path import absolute_database_path
from core.db.storage_chain_core import load_chain_tokens
from core.db.token_symbols import is_unresolved_placeholder_symbol


ROBINHOOD_NETWORK = "robinhood"
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_MARKET_ID = "dex-arbitrage-4663-uniswap-v4"
ROBINHOOD_PROTOCOL = "uniswap_v4"
ROBINHOOD_TESTNET_NETWORK = "robinhood-testnet"
ROBINHOOD_TESTNET_CHAIN_ID = 46630


def _network_for_chain_id(chain_id: int) -> str:
    return (
        ROBINHOOD_TESTNET_NETWORK
        if int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID
        else ROBINHOOD_NETWORK
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return _text(value).lower() in {"1", "true", "yes", "y", "on"}


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return value


def _token_symbol_map(database_url: str, *, chain_id: int = ROBINHOOD_CHAIN_ID) -> dict[str, str]:
    rows = load_chain_tokens(
        str(absolute_database_path(database_url)),
        network=_network_for_chain_id(chain_id),
        chain_id=chain_id,
    )
    return {
        _lower(row.get("token_address")): _text(row.get("token_symbol"))
        for row in rows
        if _lower(row.get("token_address"))
        and not is_unresolved_placeholder_symbol(row.get("token_symbol"))
    }


def _token_name_map(database_url: str, *, chain_id: int = ROBINHOOD_CHAIN_ID) -> dict[str, str]:
    rows = load_chain_tokens(
        str(absolute_database_path(database_url)),
        network=_network_for_chain_id(chain_id),
        chain_id=chain_id,
    )
    return {
        _lower(row.get("token_address")): _text(row.get("token_name"))
        for row in rows
        if _lower(row.get("token_address")) and _text(row.get("token_name"))
    }


def _token_name_missing_map(
    database_url: str,
    *,
    chain_id: int = ROBINHOOD_CHAIN_ID,
) -> dict[str, dict[str, Any]]:
    rows = load_chain_tokens(
        str(absolute_database_path(database_url)),
        network=_network_for_chain_id(chain_id),
        chain_id=chain_id,
    )
    missing: dict[str, dict[str, Any]] = {}
    for row in rows:
        address = _lower(row.get("token_address"))
        if not address:
            continue
        metadata = _json_loads(row.get("token_metadata_json") or {})
        if not isinstance(metadata, dict):
            continue
        if metadata.get("nameMissing") or metadata.get("nameFallbackSymbol"):
            missing[address] = {
                "nameMissing": True,
                "nameFallbackSymbol": _text(
                    metadata.get("nameFallbackSymbol")
                    or row.get("token_symbol")
                    or ""
                ),
            }
    return missing


__all__ = [
    "ROBINHOOD_CHAIN_ID",
    "ROBINHOOD_MARKET_ID",
    "ROBINHOOD_NETWORK",
    "ROBINHOOD_PROTOCOL",
    "ROBINHOOD_TESTNET_CHAIN_ID",
    "ROBINHOOD_TESTNET_NETWORK",
    "_bool",
    "_int",
    "_json_loads",
    "_lower",
    "_network_for_chain_id",
    "_number",
    "_text",
    "_token_name_map",
    "_token_name_missing_map",
    "_token_symbol_map",
]
