"""Official Robinhood stock-token registry helpers.

The registry is chain configuration data, not a page shard.  It maps official
stock-token addresses to the symbols/decimals published by the Robinhood
``/rhj/assets`` API so DB seeding and V4 metadata never fall back to the raw
on-chain ``0X...`` symbol of factory-deployed stock tokens.  Runtime callers
must pass ``database_url`` to read ``chain_tokens`` from SQLite; the JSON
registry file remains an explicit build/seed input only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path


NETWORK = "robinhood"
CHAIN_ID = 4663
TESTNET_NETWORK = "robinhood-testnet"
TESTNET_CHAIN_ID = 46630
STOCK_FACTORY = "0x4783C67b63dE2B358Ac5951a7D41F47A38F3C046"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolved_network_chain(
    *,
    network: str | None = None,
    chain_id: int | None = None,
) -> tuple[str, int]:
    normalized = str(network or "").strip().lower()
    if normalized == TESTNET_NETWORK or int(chain_id or 0) == TESTNET_CHAIN_ID:
        return TESTNET_NETWORK, TESTNET_CHAIN_ID
    return NETWORK, CHAIN_ID


def default_stock_token_registry_path(
    *,
    network: str | None = None,
    chain_id: int | None = None,
) -> Path:
    resolved_network, resolved_chain_id = _resolved_network_chain(
        network=network,
        chain_id=chain_id,
    )
    try:
        path = chain_data_file_path(
            resolved_network,
            "robinhood_stock_tokens",
            chain_id=resolved_chain_id,
        )
        if path is not None:
            return Path(path)
    except (TypeError, ValueError, OSError):
        pass
    return (
        Path(__file__).resolve().parents[4]
        / "chains"
        / resolved_network
        / "robinhood_stock_tokens.json"
    )


def _registry_payload_from_database(
    database_url: str,
    *,
    network: str,
    chain_id: int,
) -> dict[str, Any]:
    """Read official stock tokens from SQLite ``chain_tokens`` only."""
    from core.db.database_path import absolute_database_path
    from core.db.storage_chain_core import load_chain_tokens

    rows = load_chain_tokens(
        str(absolute_database_path(database_url)),
        network=network or NETWORK,
        chain_id=chain_id,
    )
    tokens: dict[str, dict[str, Any]] = {}
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
        if str(metadata.get("category") or "").strip().lower() != "robinhood_stock":
            continue
        try:
            decimals = int(row.get("token_decimals") or 18)
        except (TypeError, ValueError):
            decimals = 18
        tokens[symbol.lower()] = {
            "address": address,
            "symbol": symbol,
            "name": row.get("token_name") or symbol,
            "decimals": decimals,
            "category": "robinhood_stock",
            "source": "sqlite_db",
        }
    return {
        "schemaVersion": "sqlite-only",
        "token_count": len(tokens),
        "tokens": tokens,
        "source": "sqlite_db",
    }


def load_stock_token_registry(
    path: Path | str | None = None,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Return the stock registry payload with lower-case symbol keys.

    ``database_url`` makes SQLite ``chain_tokens`` the only source.  Without
    it, the explicit JSON registry path is used for build/seed workflows.
    """
    if database_url is not None:
        resolved_network, resolved_chain_id = _resolved_network_chain(
            network=network,
            chain_id=chain_id,
        )
        return _registry_payload_from_database(
            database_url,
            network=resolved_network,
            chain_id=resolved_chain_id,
        )
    registry_path = (
        Path(path)
        if path is not None
        else default_stock_token_registry_path(
            network=network,
            chain_id=chain_id,
        )
    )
    payload = _read_json(registry_path)
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    if not tokens:
        return {}
    return payload


def stock_token_by_symbol(
    path: Path | str | None = None,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    database_url: str | None = None,
) -> dict[str, dict[str, Any]]:
    payload = load_stock_token_registry(
        path=path,
        network=network,
        chain_id=chain_id,
        database_url=database_url,
    )
    result: dict[str, dict[str, Any]] = {}
    for item in (payload.get("tokens") or {}).values():
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol:
            result[symbol] = item
    return result


def stock_token_by_address(
    path: Path | str | None = None,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    database_url: str | None = None,
) -> dict[str, dict[str, Any]]:
    payload = load_stock_token_registry(
        path=path,
        network=network,
        chain_id=chain_id,
        database_url=database_url,
    )
    result: dict[str, dict[str, Any]] = {}
    for item in (payload.get("tokens") or {}).values():
        if not isinstance(item, dict):
            continue
        address = str(item.get("address") or "").strip().lower()
        if address:
            result[address] = item
    return result


def stock_token_metadata_for_address(
    address: str,
    path: Path | str | None = None,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    database_url: str | None = None,
) -> dict[str, Any] | None:
    normalized = str(address or "").strip().lower()
    return stock_token_by_address(
        path=path,
        network=network,
        chain_id=chain_id,
        database_url=database_url,
    ).get(normalized)


def apply_stock_token_metadata(
    token_rows: list[dict[str, Any]],
    *,
    path: Path | str | None = None,
    network: str | None = None,
    chain_id: int | None = None,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    """Override DB token rows whose addresses belong to official stock tokens."""
    by_address = stock_token_by_address(
        path=path,
        network=network,
        chain_id=chain_id,
        database_url=database_url,
    )
    for row in token_rows:
        if not isinstance(row, dict):
            continue
        address = str(row.get("token_address") or row.get("address") or "").strip().lower()
        meta = by_address.get(address)
        if not meta:
            continue
        symbol = str(meta.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        row["token_symbol"] = symbol
        row["token_name"] = meta.get("name") or meta.get("token_name") or symbol
        row["token_decimals"] = meta.get("decimals")
        row["token_kind"] = "erc20"
        row["is_native"] = 0
        row["is_wrapped_native"] = 0
        row["is_stable"] = 0
        row["anchor_symbol"] = None
        row["token_metadata"] = {
            "category": "robinhood_stock",
            "source": meta.get("source") or "robinhood_stock_token_registry",
            "isin": meta.get("isin"),
            "status": meta.get("status"),
        }
        row["source"] = {
            "seed": "robinhood_stock_token_registry",
            "sourcePath": str(
                default_stock_token_registry_path(
                    network=network,
                    chain_id=chain_id,
                )
            ),
        }
    return token_rows


def merge_stock_registry_into_metadata(
    metadata_payload: dict[str, Any],
    *,
    path: Path | str | None = None,
    network: str | None = None,
    chain_id: int | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Merge official stock-token rows into a ``v4_token_metadata`` payload."""
    registry = load_stock_token_registry(
        path=path,
        network=network,
        chain_id=chain_id,
        database_url=database_url,
    )
    if not registry:
        return metadata_payload
    tokens = metadata_payload.get("tokens") if isinstance(metadata_payload.get("tokens"), dict) else {}
    merged = dict(tokens)
    registry_tokens = (registry.get("tokens") or {}).values()
    for item in registry_tokens:
        if not isinstance(item, dict):
            continue
        address = str(item.get("address") or "").strip()
        symbol = str(item.get("symbol") or "").strip().upper()
        if not address or not symbol:
            continue
        merged[address.lower()] = {
            "address": address,
            "symbol": symbol,
            "name": item.get("name") or item.get("token_name"),
            "decimals": item.get("decimals"),
            "metadataOk": True,
            "source": "robinhood_stock_official",
            "isin": item.get("isin"),
            "status": item.get("status"),
        }
    metadata_payload["tokens"] = merged
    metadata_payload["tokenAddressCount"] = len(merged)
    metadata_payload["metadataOkCount"] = sum(
        1 for row in merged.values() if bool(row.get("metadataOk"))
    )
    metadata_payload["metadataMissingCount"] = max(
        0,
        int(metadata_payload.get("tokenAddressCount") or 0)
        - int(metadata_payload.get("metadataOkCount") or 0),
    )
    metadata_payload["stockTokenCount"] = sum(
        1
        for item in registry_tokens
        if isinstance(item, dict)
        and str(item.get("address") or "").strip()
        and str(item.get("symbol") or "").strip()
    )
    metadata_payload["stockTokenSource"] = str(
        default_stock_token_registry_path(
            network=network,
            chain_id=chain_id,
        )
    )
    return metadata_payload


__all__ = [
    "CHAIN_ID",
    "NETWORK",
    "STOCK_FACTORY",
    "TESTNET_CHAIN_ID",
    "TESTNET_NETWORK",
    "apply_stock_token_metadata",
    "default_stock_token_registry_path",
    "load_stock_token_registry",
    "merge_stock_registry_into_metadata",
    "_registry_payload_from_database",
    "stock_token_by_address",
    "stock_token_by_symbol",
    "stock_token_metadata_for_address",
]
