"""Python bridge to the Rust HyperSync/HyperRPC core.

The page read path is intentionally centralised here:

    page -> rs_bridge -> flashloan_rs (Rust read-only SQLite) -> SQLite

Rust is responsible for network reads and read-only SQLite reads.  SQLite
write/schema ownership stays in Python storage.  A Rust read failure is
surfaced as an error; when Rust is not installed the bridge falls back to the
Python SQLite readers only, never to JSON page shards.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.db.storage_business_io import query
from core.db.storage_v4_exchange import (
    find_v4_pools_between_tokens as _py_find_v4_pools,
    load_v4_exchange_pools as _py_load_v4_exchange_pools,
    load_v4_exchange_pool_manager as _py_load_v4_exchange_pool_manager,
    load_v4_exchange_pool_pair_counts as _py_load_v4_exchange_pool_pair_counts,
    load_v4_exchange_pool_rows_for_view as _py_load_v4_exchange_pool_rows_for_view,
    load_v4_pool_liquidity as _py_load_v4_pool_liquidity,
    load_v4_pool_liquidity_refreshed_at as _py_load_v4_pool_liquidity_refreshed_at,
    load_v4_pool_liquidity_rows_for_view as _py_load_v4_pool_liquidity_rows_for_view,
)
from core.db.storage_dex_candidate import load_dex_candidate_pools as _py_load_dex_candidate_pools
from core.db.storage_market_observation import load_market_observation as _py_load_market_observation
from core.db.storage_v4_closed_loop import load_v4_closed_loop as _py_load_v4_closed_loop
from core.local_kv import store_path


_RUST_MODULE = None
_RUST_LOAD_ERROR: str | None = None
_V4_EXCHANGE_POOL_CACHE: dict[tuple[str, str, int, str], list[dict[str, Any]]] = {}
_DEX_CANDIDATE_POOLS_CACHE: dict[tuple[str, str, int, str, str], dict[str, Any]] = {}


def _database_path(database_url: str | None = None) -> str:
    if database_url:
        return str(Path(database_url).expanduser().resolve())
    # Resolve lazily: schema seed helpers can be called while page-level
    # database-path redirects are active, and module-level import would freeze
    # whichever default was active on first import.
    from core.db.database_path import absolute_database_path

    return str(absolute_database_path(None))


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


def current_schema_version(database_url: str | None = None) -> str:
    """Return the Rust-owned business schema version."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(f"flashloan_rs unavailable; schema is Rust-owned: {rust_load_error()}")
    return str(_RUST_MODULE.current_schema_version(_database_path(database_url)))


def ensure_database_schema(database_url: str | None = None) -> dict[str, Any]:
    """Create the Rust-owned SQLite business schema tables (DDL only)."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(f"flashloan_rs unavailable; schema is Rust-owned: {rust_load_error()}")
    payload = _RUST_MODULE.ensure_database_schema(_database_path(database_url))
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs ensure_database_schema returned a non-dict payload")
    return payload


def ensure_database_indexes(database_url: str | None = None) -> dict[str, Any]:
    """Create business indexes. Must run after legacy row migration."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(f"flashloan_rs unavailable; schema is Rust-owned: {rust_load_error()}")
    payload = _RUST_MODULE.ensure_database_indexes(_database_path(database_url))
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs ensure_database_indexes returned a non-dict payload")
    return payload


def migrate_direction_schema(database_url: str | None = None) -> dict[str, Any]:
    """Run the Rust-owned legacy-to-directional SQLite migration."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; migration is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.migrate_business_direction_schema(_database_path(database_url))
    if not isinstance(payload, dict):
        raise RuntimeError(
            "flashloan_rs migrate_business_direction_schema returned a non-dict payload"
        )
    return payload


def load_v4_scan_state(
    network: str,
    chain_id: int,
    *,
    database_url: str | None = None,
    protocol: str = "uniswap_v4",
) -> dict[str, Any]:
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; V4 scan state is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.load_v4_scan_state(
        network,
        chain_id,
        _database_path(database_url),
        protocol,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs load_v4_scan_state returned a non-dict payload")
    return payload


def update_v4_scan_state(
    network: str,
    chain_id: int,
    *,
    database_url: str | None = None,
    protocol: str = "uniswap_v4",
    last_scanned_block: int | None = None,
    index_from_block: int | None = None,
    index_to_block: int | None = None,
    liquidity_scan_block: int | None = None,
    scanned_pool_count: int | None = None,
    pool_count: int | None = None,
    confirmed_liquidity_pool_count: int | None = None,
    explicit_zero_liquidity_pool_count: int | None = None,
    unscanned_pool_count: int | None = None,
    direction_candidate_limit: int = 5,
    scan_mode: str = "incremental",
) -> dict[str, Any]:
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; V4 scan state is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.update_v4_scan_state(
        network,
        chain_id,
        last_scanned_block,
        index_from_block,
        index_to_block,
        liquidity_scan_block,
        scanned_pool_count,
        pool_count,
        confirmed_liquidity_pool_count,
        explicit_zero_liquidity_pool_count,
        unscanned_pool_count,
        direction_candidate_limit,
        scan_mode,
        _database_path(database_url),
        protocol,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs update_v4_scan_state returned a non-dict payload")
    return payload


def merge_v4_pool_index(
    network: str,
    chain_id: int,
    onchain_payload: dict[str, Any],
    *,
    database_url: str | None = None,
    protocol: str = "uniswap_v4",
) -> dict[str, Any]:
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; V4 pool index merge is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.merge_v4_pool_index(
        network,
        chain_id,
        json.dumps(onchain_payload, ensure_ascii=False, separators=(",", ":")),
        _database_path(database_url),
        protocol,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs merge_v4_pool_index returned a non-dict payload")
    return payload


def build_v4_anchor_data(
    network: str,
    chain_id: int,
    *,
    protocol: str = "uniswap_v4",
    anchor_scope: str,
    market_id: str,
    anchors: list[str],
    anchor_addresses: dict[str, str],
    token_universe: list[str] | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Run the Rust-owned candidate/observation/closed-loop rebuild."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; V4 anchor data is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.build_v4_anchor_data(
        network,
        chain_id,
        protocol,
        anchor_scope,
        market_id,
        json.dumps(anchors, ensure_ascii=False, separators=(",", ":")),
        json.dumps(anchor_addresses, ensure_ascii=False, separators=(",", ":")),
        (
            json.dumps(token_universe, ensure_ascii=False, separators=(",", ":"))
            if token_universe is not None
            else None
        ),
        _database_path(database_url),
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs build_v4_anchor_data returned a non-dict payload")
    return payload


def import_v4_evidence(
    network: str,
    chain_id: int,
    token_rows: list[dict[str, Any]],
    market_rows: list[dict[str, Any]],
    pool_rows: list[dict[str, Any]],
    liquidity_rows: list[dict[str, Any]],
    index_liquidity_rows: list[dict[str, Any]],
    *,
    database_url: str | None = None,
    protocol: str = "uniswap_v4",
    replace: bool = False,
    initialize_log_count: int = 0,
) -> dict[str, Any]:
    """Run the Rust-owned raw V4 evidence import."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; V4 evidence import is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.import_v4_evidence(
        _database_path(database_url),
        network,
        chain_id,
        protocol,
        json.dumps(token_rows, ensure_ascii=False, separators=(",", ":")),
        json.dumps(market_rows, ensure_ascii=False, separators=(",", ":")),
        json.dumps(pool_rows, ensure_ascii=False, separators=(",", ":")),
        json.dumps(liquidity_rows, ensure_ascii=False, separators=(",", ":")),
        json.dumps(index_liquidity_rows, ensure_ascii=False, separators=(",", ":")),
        bool(replace),
        int(initialize_log_count or 0),
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs import_v4_evidence returned a non-dict payload")
    return payload


def prune_v4_evidence(
    network: str,
    chain_id: int,
    *,
    database_url: str | None = None,
    protocol: str = "uniswap_v4",
    delete_unknown: bool = False,
) -> dict[str, Any]:
    """Run the Rust-owned raw V4 evidence prune."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; V4 evidence prune is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.prune_v4_evidence(
        _database_path(database_url),
        network,
        chain_id,
        protocol,
        bool(delete_unknown),
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs prune_v4_evidence returned a non-dict payload")
    return payload


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


def clear_v4_exchange_pool_cache(
    database_url: str | None = None,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str = "uniswap_v4",
) -> int:
    """Drop cached static V4 pool rows after an import/rebuild."""
    cleared = _clear_dex_candidate_pools_cache(
        database_url=database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    if database_url is None:
        cleared += len(_V4_EXCHANGE_POOL_CACHE)
        _V4_EXCHANGE_POOL_CACHE.clear()
        return cleared
    path = _database_path(database_url)
    candidate_protocols = {
        str(protocol or "").strip().lower(),
        f"{str(protocol or '').strip().lower()}:view",
    }
    keys = [
        key
        for key in _V4_EXCHANGE_POOL_CACHE
        if key[0] == path
        and (network is None or key[1] == str(network).strip().lower())
        and (chain_id is None or key[2] == int(chain_id))
        and (protocol is None or key[3] in candidate_protocols)
    ]
    for key in keys:
        del _V4_EXCHANGE_POOL_CACHE[key]
    return cleared + len(keys)


def _clear_dex_candidate_pools_cache(
    database_url: str | None = None,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str = "uniswap_v4",
) -> int:
    """Drop cached static candidate-pool reads after an import/rebuild."""
    if database_url is None:
        cleared = len(_DEX_CANDIDATE_POOLS_CACHE)
        _DEX_CANDIDATE_POOLS_CACHE.clear()
        return cleared
    path = _database_path(database_url)
    keys = [
        key
        for key in _DEX_CANDIDATE_POOLS_CACHE
        if key[0] == path
        and (network is None or key[1] == str(network).strip().lower())
        and (chain_id is None or key[2] == int(chain_id))
        and (protocol is None or key[3] == str(protocol).strip().lower())
    ]
    for key in keys:
        del _DEX_CANDIDATE_POOLS_CACHE[key]
    return len(keys)


def load_v4_exchange_pools(
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    cache_key = (
        _database_path(database_url),
        str(network or "").strip().lower(),
        int(chain_id or 0),
        str(protocol or "").strip().lower(),
    )
    cached = _V4_EXCHANGE_POOL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    _load_rust()
    if _RUST_MODULE is not None:
        try:
            payload = _RUST_MODULE.load_v4_exchange_pools(
                network,
                chain_id,
                protocol,
                _database_path(database_url),
            )
            rows = [_convert_rust_dict(row) for row in _rust_rows(payload, "pools")]
            _V4_EXCHANGE_POOL_CACHE[cache_key] = rows
            return rows
        except Exception as exc:
            raise RuntimeError(
                "flashloan_rs read failed for load_v4_exchange_pools; "
                "SQLite is authoritative and Rust read errors must not be hidden. "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    rows = _py_load_v4_exchange_pools(
        _database_path(database_url),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    _V4_EXCHANGE_POOL_CACHE[cache_key] = rows
    return rows


def load_v4_exchange_pool_rows_for_view(
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    cache_key = (
        _database_path(database_url),
        str(network or "").strip().lower(),
        int(chain_id or 0),
        f"{str(protocol or '').strip().lower()}:view",
    )
    cached = _V4_EXCHANGE_POOL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    _load_rust()
    if _RUST_MODULE is not None and hasattr(_RUST_MODULE, "load_v4_exchange_pool_rows_for_view"):
        try:
            payload = _RUST_MODULE.load_v4_exchange_pool_rows_for_view(
                network,
                chain_id,
                protocol,
                _database_path(database_url),
            )
            rows = [_convert_rust_dict(row) for row in _rust_rows(payload, "pools")]
        except Exception as exc:
            raise RuntimeError(
                "flashloan_rs read failed for load_v4_exchange_pool_rows_for_view; "
                "SQLite is authoritative and Rust read errors must not be hidden. "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    else:
        rows = _py_load_v4_exchange_pool_rows_for_view(
            _database_path(database_url),
            network=network,
            chain_id=chain_id,
            protocol=protocol,
        )
    _V4_EXCHANGE_POOL_CACHE[cache_key] = rows
    return rows


def load_v4_exchange_pool_pair_counts(
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    _load_rust()
    if _RUST_MODULE is not None and hasattr(_RUST_MODULE, "load_v4_exchange_pool_pair_counts"):
        try:
            payload = _RUST_MODULE.load_v4_exchange_pool_pair_counts(
                network,
                chain_id,
                protocol,
                _database_path(database_url),
            )
            return [_convert_rust_dict(row) for row in _rust_rows(payload, "pairs")]
        except Exception as exc:
            raise RuntimeError(
                "flashloan_rs read failed for load_v4_exchange_pool_pair_counts; "
                "SQLite is authoritative and Rust read errors must not be hidden. "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    return _py_load_v4_exchange_pool_pair_counts(
        _database_path(database_url),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )


def load_v4_exchange_pool_manager(
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    _load_rust()
    if _RUST_MODULE is not None and hasattr(_RUST_MODULE, "load_v4_exchange_pool_manager"):
        try:
            payload = _RUST_MODULE.load_v4_exchange_pool_manager(
                network,
                chain_id,
                protocol,
                _database_path(database_url),
            )
            manager = payload.get("manager") if isinstance(payload, dict) else payload
            return manager if isinstance(manager, dict) else {}
        except Exception as exc:
            raise RuntimeError(
                "flashloan_rs read failed for load_v4_exchange_pool_manager; "
                "SQLite is authoritative and Rust read errors must not be hidden. "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    return _py_load_v4_exchange_pool_manager(
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
            raise RuntimeError(
                "flashloan_rs read failed for load_v4_pool_liquidity; "
                "SQLite is authoritative and Rust read errors must not be hidden. "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    return _py_load_v4_pool_liquidity(
        _database_path(database_url),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )


def load_v4_pool_liquidity_rows_for_view(
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    _load_rust()
    if _RUST_MODULE is not None and hasattr(_RUST_MODULE, "load_v4_pool_liquidity_rows_for_view"):
        try:
            payload = _RUST_MODULE.load_v4_pool_liquidity_rows_for_view(
                network,
                chain_id,
                protocol,
                _database_path(database_url),
            )
            return [_convert_rust_dict(row) for row in _rust_rows(payload, "liquidity")]
        except Exception as exc:
            raise RuntimeError(
                "flashloan_rs read failed for load_v4_pool_liquidity_rows_for_view; "
                "SQLite is authoritative and Rust read errors must not be hidden. "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    return _py_load_v4_pool_liquidity_rows_for_view(
        _database_path(database_url),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )


def load_v4_pool_liquidity_refreshed_at(
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    *,
    database_url: str | None = None,
) -> str | None:
    _load_rust()
    if _RUST_MODULE is not None and hasattr(_RUST_MODULE, "load_v4_pool_liquidity_refreshed_at"):
        try:
            payload = _RUST_MODULE.load_v4_pool_liquidity_refreshed_at(
                network,
                chain_id,
                protocol,
                _database_path(database_url),
            )
            value = payload.get("refreshedAt", payload.get("refreshed_at")) if isinstance(payload, dict) else payload
            return str(value) if value not in (None, "") else None
        except Exception as exc:
            raise RuntimeError(
                "flashloan_rs read failed for load_v4_pool_liquidity_refreshed_at; "
                "SQLite is authoritative and Rust read errors must not be hidden. "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    return _py_load_v4_pool_liquidity_refreshed_at(
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
    cache_key = (
        _database_path(database_url),
        str(network or "").strip().lower(),
        int(chain_id or 0),
        str(protocol or "").strip().lower(),
        str(anchor_symbol or "").strip().upper(),
    )
    cached = _DEX_CANDIDATE_POOLS_CACHE.get(cache_key)
    if cached is not None:
        return cached
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
                "abnormalAnchorTokens": _rust_rows(payload, "abnormalAnchorTokens"),
                "abnormalPairs": _rust_rows(payload, "abnormalPairs"),
                "abnormalPools": _rust_rows(payload, "abnormalPools"),
            }
            _DEX_CANDIDATE_POOLS_CACHE[cache_key] = {**_source_meta([], None), **rows}
            return _DEX_CANDIDATE_POOLS_CACHE[cache_key]
        except Exception as exc:
            raise RuntimeError(
                "flashloan_rs read failed for load_dex_candidate_pools; "
                "SQLite is authoritative and Rust read errors must not be hidden. "
                f"{type(exc).__name__}: {exc}"
            ) from exc
    rows = _py_load_dex_candidate_pools(
        _database_path(database_url),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        anchor_symbol=anchor_symbol,
    )
    _DEX_CANDIDATE_POOLS_CACHE[cache_key] = {**_source_meta([], "python_sqlite_fallback"), **rows}
    return _DEX_CANDIDATE_POOLS_CACHE[cache_key]


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
                "abnormalPairs": _rust_rows(payload, "abnormalPairs"),
                "abnormalPools": _rust_rows(payload, "abnormalPools"),
            }
            return {**_source_meta([], None), **rows}
        except Exception as exc:
            raise RuntimeError(
                "flashloan_rs read failed for load_market_observation; "
                "SQLite is authoritative and Rust read errors must not be hidden. "
                f"{type(exc).__name__}: {exc}"
            ) from exc
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
            raise RuntimeError(
                "flashloan_rs read failed for load_v4_closed_loop; "
                "SQLite is authoritative and Rust read errors must not be hidden. "
                f"{type(exc).__name__}: {exc}"
            ) from exc
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
            raise RuntimeError(
                "flashloan_rs read failed for find_pools_between_tokens; "
                "SQLite is authoritative and Rust read errors must not be hidden. "
                f"{type(exc).__name__}: {exc}"
            ) from exc
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


def resolve_private_key_hex(private_key: str) -> dict[str, Any]:
    """Resolve an Ethereum private key to its address through the Rust signer."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; signing is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.resolve_private_key_hex(str(private_key))
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs resolve_private_key_hex returned a non-dict payload")
    return payload


def sign_typed_transaction_json(
    transaction: dict[str, Any],
    private_key: str,
) -> dict[str, Any]:
    """Sign a JSON-serializable EIP-1559/EIP-155 transaction in Rust."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; signing is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.sign_typed_transaction_json(
        json.dumps(transaction, ensure_ascii=False, separators=(",", ":")),
        str(private_key),
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs sign_typed_transaction_json returned a non-dict payload")
    return payload


def build_v4_quote_calldata(request: dict[str, Any]) -> dict[str, Any]:
    """Build Uniswap V4 quoteExactInputSingle calldata in Rust."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; V4 quote calldata is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.build_v4_quote_calldata(
        json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs build_v4_quote_calldata returned a non-dict payload")
    return payload


def quote_v4_exact_input_single(
    request: dict[str, Any],
    rpc_url: str,
    quoter_address: str,
    *,
    call_from: str | None = None,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    """Run the Rust V4 quote eth_call and return ok/error/revertData."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; V4 quote execution is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.quote_v4_exact_input_single(
        json.dumps(request, ensure_ascii=False, separators=(",", ":")),
        str(rpc_url),
        str(quoter_address),
        call_from,
        int(timeout_seconds or 20),
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs quote_v4_exact_input_single returned a non-dict payload")
    return payload


def submit_v4_route(
    config: dict[str, Any],
    raw_transaction: str,
    relay_urls: str | None = None,
) -> dict[str, Any]:
    """Broadcast a signed V4 route through Rust private/public RPC handling."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; route broadcast is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.submit_v4_route(
        json.dumps(config, ensure_ascii=False, separators=(",", ":")),
        str(raw_transaction),
        relay_urls,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs submit_v4_route returned a non-dict payload")
    return payload


def load_v4_receipt(
    rpc_urls: list[str],
    tx_hash: str,
    *,
    wait: bool = False,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    """Load a V4 route receipt through Rust; wait=True polls until timeout."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; receipt loading is Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.load_v4_receipt(
        json.dumps([str(url) for url in rpc_urls], ensure_ascii=False, separators=(",", ":")),
        str(tx_hash),
        bool(wait),
        int(timeout_seconds or 20),
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs load_v4_receipt returned a non-dict payload")
    return payload


def get_v4_pending_nonce(
    rpc_url: str,
    signer_address: str,
    *,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    """Read the pending nonce through Rust for the V4 execution path."""
    _load_rust()
    if _RUST_MODULE is None:
        raise RuntimeError(
            f"flashloan_rs unavailable; nonce reads are Rust-owned: {rust_load_error()}"
        )
    payload = _RUST_MODULE.get_v4_pending_nonce(
        str(rpc_url),
        str(signer_address),
        int(timeout_seconds or 20),
    )
    if not isinstance(payload, dict):
        raise RuntimeError("flashloan_rs get_v4_pending_nonce returned a non-dict payload")
    return payload


def page_payload_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ok": True, "rows": rows, "source": "sqlite_db"}


__all__ = [
    "build_v4_quote_calldata",
    "build_v4_pool_index",
    "find_pools_between_tokens",
    "get_v4_pending_nonce",
    "load_dex_candidate_pools",
    "load_market_observation",
    "load_v4_closed_loop",
    "load_v4_exchange_pools",
    "load_v4_exchange_pool_manager",
    "load_v4_exchange_pool_pair_counts",
    "load_v4_exchange_pool_rows_for_view",
    "load_v4_pool_liquidity",
    "load_v4_pool_liquidity_refreshed_at",
    "load_v4_pool_liquidity_rows_for_view",
    "load_v4_receipt",
    "page_payload_from_rows",
    "quote_v4_exact_input_single",
    "resolve_private_key_hex",
    "rust_available",
    "current_schema_version",
    "ensure_database_schema",
    "ensure_database_indexes",
    "migrate_direction_schema",
    "load_v4_scan_state",
    "update_v4_scan_state",
    "merge_v4_pool_index",
    "sign_typed_transaction_json",
    "submit_v4_route",
    "rust_load_error",
]
