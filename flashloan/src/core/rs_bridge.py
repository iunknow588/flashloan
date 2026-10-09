"""Python bridge to the Rust HyperSync/HyperRPC core.

The page read path is intentionally centralised here:

    page -> rs_bridge -> flashloan_rs (Rust read-only SQLite) -> SQLite

When the Rust extension is unavailable or the database tables are missing,
the bridge falls back to the existing Python storage/JSON readers.  Writes
always stay in Python storage and never go through Rust.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.db.storage_business_io import query
from core.db.database_path import absolute_database_path
from core.db.storage_v4_exchange import (
    find_v4_pools_between_tokens as _py_find_v4_pools,
    load_v4_exchange_pools as _py_load_v4_exchange_pools,
    load_v4_pool_liquidity as _py_load_v4_pool_liquidity,
)
from core.db.storage_dex_candidate import load_dex_candidate_pools as _py_load_dex_candidate_pools
from core.db.storage_market_observation import load_market_observation as _py_load_market_observation
from core.db.storage_v4_closed_loop import load_v4_closed_loop as _py_load_v4_closed_loop
from core.local_kv import store_path


_RUST_MODULE = None
_RUST_LOAD_ERROR: str | None = None


def _database_path(database_url: str | None = None) -> str:
    return str(absolute_database_path(database_url))


def rust_available() -> bool:
    _load_rust()
    return _RUST_MODULE is not None


def _load_rust() -> None:
    global _RUST_MODULE, _RUST_LOAD_ERROR
    if _RUST_MODULE is not None or _RUST_LOAD_ERROR is not None:
        return
    try:
        import flashloan_rs  # type: ignore

        _RUST_MODULE = flashloan_rs
        _RUST_LOAD_ERROR = None
    except Exception as exc:  # pragma: no cover - depends on local build
        _RUST_LOAD_ERROR = f"{type(exc).__name__}: {exc}"


def rust_load_error() -> str | None:
    _load_rust()
    return _RUST_LOAD_ERROR


def _rust_rows(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get(key)
    else:
        rows = payload
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(row)
    return normalized


def _source_meta(rows: list[dict[str, Any]], fallback_reason: str | None) -> dict[str, Any]:
    return {
        "source": "rust_sqlite" if _RUST_MODULE is not None and fallback_reason is None else "python_sqlite",
        "fallbackReason": fallback_reason,
    }


def _camel_payload(payload: Any) -> Any:
    """Convert Rust SQLite row keys to the camelCase page payload keys."""
    if isinstance(payload, dict):
        return {
            key.replace("_", " ") if isinstance(key, str) else key: _camel_payload(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_camel_payload(item) for item in payload]
    return payload


def _rust_snake_to_camel(text: str) -> str:
    parts = text.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _convert_rust_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {_rust_snake_to_camel(str(key)): value for key, value in row.items()}


def load_v4_exchange_pools(
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    _load_rust()
    if _RUST_MODULE is not None:
        try:
            payload = _RUST_MODULE.load_v4_exchange_pools(
                network,
                chain_id,
                protocol,
                _database_path(database_url),
            )
            return [_convert_rust_dict(row) for row in _rust_rows(payload, "pools")]
        except Exception as exc:
            _RUST_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
    return _py_load_v4_exchange_pools(
        _database_path(database_url),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )


def load_v4_pool_liquidity(
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    _load_rust()
    if _RUST_MODULE is not None:
        try:
            payload = _RUST_MODULE.load_v4_pool_liquidity(
                network,
                chain_id,
                protocol,
                _database_path(database_url),
            )
            return [_convert_rust_dict(row) for row in _rust_rows(payload, "liquidity")]
        except Exception as exc:
            _RUST_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
    return _py_load_v4_pool_liquidity(
        _database_path(database_url),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )


def load_dex_candidate_pools(
    network: str,
    chain_id: int,
    protocol: str,
    anchor_symbol: str,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    _load_rust()
    if _RUST_MODULE is not None:
        try:
            payload = _RUST_MODULE.load_dex_candidate_pools(
                network,
                chain_id,
                protocol,
                anchor_symbol,
                _database_path(database_url),
            )
            rows = {
                "anchorTokens": _rust_rows(payload, "anchorTokens"),
                "pairs": _rust_rows(payload, "pairs"),
                "pools": _rust_rows(payload, "pools"),
                "recommendations": _rust_rows(payload, "recommendations"),
            }
            return {**_source_meta([], None), **rows}
        except Exception as exc:
            _RUST_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
    rows = _py_load_dex_candidate_pools(
        _database_path(database_url),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        anchor_symbol=anchor_symbol,
    )
    return {**_source_meta([], "python_sqlite_fallback"), **rows}


def load_market_observation(
    network: str,
    chain_id: int,
    protocol: str,
    anchor_symbol: str,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    _load_rust()
    if _RUST_MODULE is not None:
        try:
            payload = _RUST_MODULE.load_market_observation(
                network,
                chain_id,
                protocol,
                anchor_symbol,
                _database_path(database_url),
            )
            rows = {
                "pairs": _rust_rows(payload, "pairs"),
                "pools": _rust_rows(payload, "pools"),
                "liquidity": _rust_rows(payload, "liquidity"),
            }
            return {**_source_meta([], None), **rows}
        except Exception as exc:
            _RUST_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
    rows = _py_load_market_observation(
        _database_path(database_url),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        anchor_symbol=anchor_symbol,
    )
    return {**_source_meta([], "python_sqlite_fallback"), **rows}


def load_v4_closed_loop(
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    _load_rust()
    if _RUST_MODULE is not None:
        try:
            payload = _RUST_MODULE.load_v4_closed_loop(
                network,
                chain_id,
                protocol,
                market_id,
                _database_path(database_url),
            )
            rows = {
                "loops": _rust_rows(payload, "loops"),
                "hops": _rust_rows(payload, "hops"),
                "optimizations": _rust_rows(payload, "optimizations"),
            }
            return {**_source_meta([], None), **rows}
        except Exception as exc:
            _RUST_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
    rows = _py_load_v4_closed_loop(
        _database_path(database_url),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
    )
    return {**_source_meta([], "python_sqlite_fallback"), **rows}


def find_pools_between_tokens(
    network: str,
    chain_id: int,
    token_a: str,
    token_b: str,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    _load_rust()
    if _RUST_MODULE is not None:
        try:
            payload = _RUST_MODULE.find_pools_between_tokens(
                network,
                chain_id,
                token_a,
                token_b,
                _database_path(database_url),
            )
            if isinstance(payload, dict):
                return payload
        except Exception as exc:
            _RUST_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
    pools = _py_find_v4_pools(
        _database_path(database_url),
        network=network,
        chain_id=chain_id,
        protocol="uniswap_v4",
        token_a=token_a,
        token_b=token_b,
    )
    return {
        "ok": True,
        "poolCount": len(pools),
        "pools": pools,
    }


def build_v4_pool_index(
    network: str,
    chain_id: int,
    pool_manager: str,
    *,
    database_url: str | None = None,
    data_source: str = "hyperrpc",
    from_block: int = 0,
    to_block: int | None = None,
    currency0: str | None = None,
    currency1: str | None = None,
) -> dict[str, Any]:
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            "flashloan_rs is not available; build_v4_pool_index requires the Rust extension. "
            f"rust_load_error={rust_load_error()}"
        )
    payload = _RUST_MODULE.build_v4_pool_index(
        network,
        chain_id,
        pool_manager,
        data_source,
        int(from_block),
        int(to_block) if to_block is not None else None,
        _database_path(database_url),
        currency0,
        currency1,
    )
    return payload if isinstance(payload, dict) else {"ok": False, "payload": payload}


def page_payload_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ok": True, "rows": rows, "source": "sqlite_db"}


__all__ = [
    "build_v4_pool_index",
    "find_pools_between_tokens",
    "load_dex_candidate_pools",
    "load_market_observation",
    "load_v4_closed_loop",
    "load_v4_exchange_pools",
    "load_v4_pool_liquidity",
    "page_payload_from_rows",
    "rust_available",
    "rust_load_error",
]
