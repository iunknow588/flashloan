"""Rust-first raw V4 evidence import/prune for the formal refresh path.

Python keeps pure token/pool/market normalization; ``flashloan_rs`` owns the
SQLite writes when installed.  JSON evidence files are never read here.
"""

from __future__ import annotations

from typing import Any

from core.db.import_existing_json import (
    _liquidity_rows,
    _liquidity_rows_from_index,
    _network_config,
    _token_rows_from_v4_index,
    _v4_pool_rows_from_index,
)
from core.db.storage_chain_markets import _builtin_chain_market_rows
from core.rs_bridge import (
    import_v4_evidence as _rust_import_v4_evidence,
    prune_v4_evidence as _rust_prune_v4_evidence,
    rust_available,
)
from dex.intent_trade.chains.robinhood_common.stock_token_registry import (
    apply_stock_pool_metadata,
)


def prepare_v4_evidence_rows(
    *,
    network: str,
    chain_id: int,
    index_payload: dict[str, Any] | None = None,
    liquidity_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return canonical row arrays for the Rust V4 evidence importer."""
    config = _network_config(network)
    wanted_network = str(config["network"])
    wanted_chain_id = int(config["chain_id"])
    if int(chain_id or 0) != wanted_chain_id:
        raise ValueError(
            f"prepare_v4_evidence_rows chain_id={chain_id} does not match "
            f"{wanted_network} chain_id={wanted_chain_id}"
        )
    index = index_payload if isinstance(index_payload, dict) else {}
    liquidity = liquidity_payload if isinstance(liquidity_payload, dict) else {}

    token_rows = _token_rows_from_v4_index(
        index,
        anchor_symbols=tuple(config["anchor_symbols"]),
        stable_symbols=set(config["stable_symbols"]),
        apply_stock_metadata=wanted_network in {"robinhood", "robinhood-testnet"},
        stock_network=wanted_network,
        stock_chain_id=wanted_chain_id,
    )
    market_rows = [
        row
        for row in _builtin_chain_market_rows()
        if str(row.get("network") or "").strip().lower() == wanted_network
        and int(row.get("chain_id") or -1) == wanted_chain_id
    ]
    pool_rows = _v4_pool_rows_from_index(index)
    apply_stock_pool_metadata(
        pool_rows,
        network=wanted_network,
        chain_id=wanted_chain_id,
    )
    liquidity_rows = _liquidity_rows(liquidity)
    index_liquidity_rows = [
        row
        for row in _liquidity_rows_from_index(index)
        if row.get("has_liquidity") is not None
    ]
    return {
        "network": wanted_network,
        "chainId": wanted_chain_id,
        "token_rows": token_rows,
        "market_rows": market_rows,
        "pool_rows": pool_rows,
        "liquidity_rows": liquidity_rows,
        "index_liquidity_rows": index_liquidity_rows,
        "initialize_log_count": int(index.get("initializeLogCount") or 0),
    }


def import_v4_evidence_rows(
    *,
    database: str,
    network: str,
    chain_id: int,
    index_payload: dict[str, Any] | None = None,
    liquidity_payload: dict[str, Any] | None = None,
    protocol: str = "uniswap_v4",
    replace: bool = False,
) -> dict[str, Any]:
    """Import in-memory V4 evidence; Rust owns the SQLite write when installed."""
    if rust_available():
        rows = prepare_v4_evidence_rows(
            network=network,
            chain_id=chain_id,
            index_payload=index_payload,
            liquidity_payload=liquidity_payload,
        )
        return _rust_import_v4_evidence(
            rows["network"],
            rows["chainId"],
            rows["token_rows"],
            rows["market_rows"],
            rows["pool_rows"],
            rows["liquidity_rows"],
            rows["index_liquidity_rows"],
            database_url=database,
            protocol=protocol,
            replace=replace,
            initialize_log_count=rows["initialize_log_count"],
        )
    from core.db.import_v4_evidence import import_v4_evidence_payloads

    return import_v4_evidence_payloads(
        str(database),
        network=network,
        chain_id=chain_id,
        index_payload=index_payload,
        liquidity_payload=liquidity_payload,
        protocol=protocol,
        replace=replace,
    )


def prune_v4_evidence_rows(
    *,
    database: str,
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    delete_unknown: bool = False,
) -> dict[str, Any]:
    """Prune non-executable V4 evidence; Rust owns the SQLite write when installed."""
    if rust_available():
        return _rust_prune_v4_evidence(
            network,
            chain_id,
            database_url=database,
            protocol=protocol,
            delete_unknown=delete_unknown,
        )
    from core.db.storage_v4_exchange import prune_v4_non_executable_evidence

    return prune_v4_non_executable_evidence(
        str(database),
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        delete_unknown=delete_unknown,
    )


__all__ = [
    "import_v4_evidence_rows",
    "prepare_v4_evidence_rows",
    "prune_v4_evidence_rows",
]
