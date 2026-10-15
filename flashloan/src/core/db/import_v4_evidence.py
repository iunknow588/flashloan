"""Import in-memory V4 on-chain evidence payloads into SQLite.

The chain evidence builders return fully constructed index/liquidity payloads
in memory. This module writes those payloads directly into the raw V4
exchange tables and seeds chain tokens/markets. It never reads runtime JSON
files; JSON evidence files are optional audit artifacts only and are not an
input to business-table rebuilds.
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
from core.db.storage_chain_core import (
    seed_chain_markets_from_builtin,
    seed_chain_tokens,
    upsert_initialization,
)
from core.db.storage_v4_exchange import (
    clear_v4_exchange_evidence,
    upsert_v4_exchange_pools,
    upsert_v4_liquidity_snapshots,
)
from dex.intent_trade.chains.robinhood_common.stock_token_registry import (
    apply_stock_pool_metadata,
)


def import_v4_evidence_payloads(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    index_payload: dict[str, Any] | None = None,
    liquidity_payload: dict[str, Any] | None = None,
    protocol: str = "uniswap_v4",
    replace: bool = False,
) -> dict[str, Any]:
    """Write freshly built V4 evidence payloads into SQLite without JSON files.

    The index payload is required for pool/token seeding; the liquidity
    payload is optional and only refreshes liquidity snapshots.
    """
    config = _network_config(network)
    wanted_network = str(config["network"])
    wanted_chain_id = int(config["chain_id"])
    if int(chain_id or 0) != wanted_chain_id:
        raise ValueError(
            f"import_v4_evidence_payloads chain_id={chain_id} does not match "
            f"{wanted_network} chain_id={wanted_chain_id}"
        )
    if replace:
        clear_v4_exchange_evidence(
            database_url,
            network=wanted_network,
            chain_id=wanted_chain_id,
            protocol=protocol,
        )
    index = index_payload if isinstance(index_payload, dict) else {}
    liquidity = liquidity_payload if isinstance(liquidity_payload, dict) else {}
    anchor_symbols = tuple(config["anchor_symbols"])
    anchor_addresses = config["anchor_addresses"]
    stable_symbols = config["stable_symbols"]

    token_rows = _token_rows_from_v4_index(
        index,
        anchor_symbols=anchor_symbols,
        stable_symbols=stable_symbols,
        apply_stock_metadata=wanted_network in {"robinhood", "robinhood-testnet"},
        stock_network=wanted_network,
        stock_chain_id=wanted_chain_id,
    )
    seed_chain_tokens(
        database_url,
        network=wanted_network,
        chain_id=wanted_chain_id,
        token_rows=token_rows,
    )
    seed_chain_markets_from_builtin(
        database_url,
        network=wanted_network,
        chain_id=wanted_chain_id,
    )
    pool_rows = _v4_pool_rows_from_index(index)
    apply_stock_pool_metadata(
        pool_rows,
        network=wanted_network,
        chain_id=wanted_chain_id,
    )
    v4_pool_count = upsert_v4_exchange_pools(
        database_url,
        network=wanted_network,
        chain_id=wanted_chain_id,
        protocol=protocol,
        pool_rows=pool_rows,
    )
    # Liquidity file rows are written first, then authoritative index rows.
    # The storage upsert preserves explicit evidence instead of letting an
    # empty/stale document turn real depth into a false negative.
    upsert_v4_liquidity_snapshots(
        database_url,
        network=wanted_network,
        chain_id=wanted_chain_id,
        protocol=protocol,
        rows=_liquidity_rows(liquidity),
    )
    # The index may carry live liquidity markers from an on-chain scan. When
    # the index was reused from SQLite (--skip-index), rows without explicit
    # evidence must not be written back as weak sqlite_db snapshots. Only
    # explicit has_liquidity rows are real evidence.
    index_liquidity_rows = [
        row
        for row in _liquidity_rows_from_index(index)
        if row.get("has_liquidity") is not None
    ]
    upsert_v4_liquidity_snapshots(
        database_url,
        network=wanted_network,
        chain_id=wanted_chain_id,
        protocol=protocol,
        rows=index_liquidity_rows,
    )
    upsert_initialization(
        database_url,
        network=wanted_network,
        chain_id=wanted_chain_id,
        protocol=protocol,
        domain="v4_exchange_pool_index",
        status="complete",
        completeness={
            "initialize_log_count": index.get("initializeLogCount") or 0,
            "pool_count": v4_pool_count,
        },
    )
    return {
        "ok": True,
        "network": wanted_network,
        "chainId": wanted_chain_id,
        "v4PoolCount": v4_pool_count,
        "tokenCount": len(token_rows),
        "source": "onchain_evidence_payload",
        "detail": "raw V4 tables imported directly from in-memory evidence; JSON files are not read",
    }


__all__ = ["import_v4_evidence_payloads"]
