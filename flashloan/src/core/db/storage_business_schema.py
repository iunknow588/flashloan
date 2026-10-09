"""SQLite business tables for chain, V4 exchange, candidate, observation and loop data.

Schema ownership stays in Python: this module creates and migrates the
independent business tables. The Rust core only reads them through
``SQLITE_OPEN_READONLY`` and never executes DDL/DML.
"""

from __future__ import annotations

import sqlite3
from typing import Any


BUSINESS_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS chain_registry_meta (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        schema_version TEXT NOT NULL,
        primary_key TEXT NOT NULL,
        registry_file TEXT NOT NULL,
        defaults_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chain_registry (
        chain_id INTEGER NOT NULL,
        network TEXT NOT NULL,
        directory TEXT NOT NULL,
        chain_file TEXT NOT NULL,
        flow_file TEXT NOT NULL,
        registry_file TEXT NOT NULL,
        is_enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (chain_id),
        UNIQUE (network)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chain_configs (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        schema_version TEXT,
        display_name TEXT,
        native_symbol TEXT,
        protocol_mask INTEGER NOT NULL DEFAULT 1,
        chain_dir TEXT,
        data_file TEXT,
        explorer_url TEXT,
        rpc_json TEXT NOT NULL,
        contracts_json TEXT,
        features_json TEXT,
        flash_loan_json TEXT,
        data_files_json TEXT,
        processing_handlers_json TEXT,
        normalized_json TEXT NOT NULL,
        checksum TEXT,
        loaded_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network),
        UNIQUE (chain_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chain_info (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        global_chain_id TEXT NOT NULL,
        chain_key TEXT NOT NULL,
        schema_version TEXT,
        display_name TEXT,
        native_symbol TEXT,
        rpc_url TEXT,
        explorer_url TEXT,
        rpc_json TEXT NOT NULL,
        protocol_mask INTEGER NOT NULL DEFAULT 1,
        protocols_json TEXT NOT NULL DEFAULT '[]',
        default_protocols_json TEXT NOT NULL DEFAULT '[]',
        features_json TEXT,
        flash_loan_json TEXT,
        contracts_json TEXT,
        processing_handlers_json TEXT,
        data_files_json TEXT,
        market_ids_json TEXT NOT NULL DEFAULT '[]',
        default_market_id TEXT,
        chain_file TEXT,
        flow_file TEXT,
        data_file TEXT,
        ui_payload_json TEXT,
        checksum TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network),
        UNIQUE (chain_id),
        UNIQUE (global_chain_id),
        UNIQUE (network, chain_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chain_tokens (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        token_address TEXT NOT NULL,
        token_symbol TEXT NOT NULL,
        token_name TEXT,
        token_decimals INTEGER,
        token_kind TEXT NOT NULL DEFAULT 'erc20',
        is_native INTEGER NOT NULL DEFAULT 0,
        is_wrapped_native INTEGER NOT NULL DEFAULT 0,
        is_stable INTEGER NOT NULL DEFAULT 0,
        anchor_symbol TEXT,
        token_metadata_json TEXT,
        source_json TEXT,
        first_seen_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, token_address),
        FOREIGN KEY (network, chain_id)
            REFERENCES chain_info (network, chain_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chain_contracts (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        contract_key TEXT NOT NULL,
        contract_address TEXT NOT NULL,
        is_zero_address INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, contract_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chain_data_files (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        file_key TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        absolute_path TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, file_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chain_flows (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        flow_name TEXT NOT NULL,
        handler TEXT NOT NULL,
        allowed_protocols_json TEXT NOT NULL,
        data_file_keys_json TEXT NOT NULL,
        requires_scope_match INTEGER NOT NULL DEFAULT 0,
        flow_config_json TEXT,
        isolation_json TEXT,
        flow_file TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, flow_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chain_markets (
        market_id TEXT NOT NULL,
        canonical_market_id TEXT NOT NULL,
        legacy_market_id TEXT,
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        feature TEXT NOT NULL,
        protocol TEXT NOT NULL,
        protocol_id TEXT,
        display_name TEXT,
        market_type TEXT,
        native_symbol TEXT,
        observation_only INTEGER NOT NULL DEFAULT 0,
        protocol_supported INTEGER NOT NULL DEFAULT 0,
        chain_default INTEGER NOT NULL DEFAULT 0,
        market_default INTEGER NOT NULL DEFAULT 0,
        rpc_env_prefixes_json TEXT,
        migration_type TEXT,
        market_config_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (market_id),
        UNIQUE (network, chain_id, feature, protocol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chain_business_initialization (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL DEFAULT '',
        domain TEXT NOT NULL,
        status TEXT NOT NULL,
        completeness_json TEXT NOT NULL,
        error_json TEXT,
        initialized_at TEXT,
        refreshed_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, domain)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS v4_exchange_pools (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL DEFAULT 'uniswap_v4',
        pool_id TEXT NOT NULL,
        pool_identity_mode TEXT NOT NULL DEFAULT 'v4_pool_id',
        pool_manager TEXT,
        pool_key_json TEXT,
        token0 TEXT,
        token1 TEXT,
        fee INTEGER,
        fee_label TEXT,
        dynamic_fee INTEGER NOT NULL DEFAULT 0,
        special_fee INTEGER NOT NULL DEFAULT 0,
        tick_spacing INTEGER,
        hooks TEXT,
        address_pair TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, pool_id),
        FOREIGN KEY (network, chain_id, token0)
            REFERENCES chain_tokens (network, chain_id, token_address),
        FOREIGN KEY (network, chain_id, token1)
            REFERENCES chain_tokens (network, chain_id, token_address)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS v4_exchange_pool_liquidity_snapshots (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL DEFAULT 'uniswap_v4',
        pool_id TEXT NOT NULL,
        has_liquidity INTEGER,
        liquidity TEXT,
        liquidity_index REAL,
        estimated_tvl_usd REAL,
        balances_json TEXT,
        depth_status TEXT,
        depth_band_json TEXT,
        depth_score_usd REAL,
        sqrt_price_x96 TEXT,
        price REAL,
        tick INTEGER,
        liquidity_read_at TEXT,
        liquidity_block_number INTEGER,
        recent_swap_active INTEGER,
        observed_at TEXT NOT NULL,
        refreshed_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, pool_id),
        FOREIGN KEY (network, chain_id, protocol, pool_id)
            REFERENCES v4_exchange_pools (network, chain_id, protocol, pool_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS v4_closed_loops (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL DEFAULT 'uniswap_v4',
        market_id TEXT NOT NULL,
        loop_key TEXT NOT NULL,
        anchor_address TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        path_text TEXT,
        path_addresses_json TEXT,
        path_symbols_json TEXT,
        pool_path_key TEXT,
        pool_ids_json TEXT,
        hop_candidate_counts_json TEXT,
        hop_candidate_combination_count INTEGER,
        recommendation_basis TEXT,
        recommended_first_priority INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        source TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, market_id, loop_key),
        FOREIGN KEY (network, chain_id, anchor_address)
            REFERENCES chain_tokens (network, chain_id, token_address)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS v4_closed_loop_hops (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL DEFAULT 'uniswap_v4',
        market_id TEXT NOT NULL,
        loop_key TEXT NOT NULL,
        hop_index INTEGER NOT NULL,
        hop_name TEXT NOT NULL,
        input_address TEXT NOT NULL,
        output_address TEXT NOT NULL,
        pool_id TEXT NOT NULL,
        base_pool_id TEXT NOT NULL,
        pool_selection_basis TEXT,
        optimization_id TEXT,
        temporary_override INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, market_id, loop_key, hop_index),
        FOREIGN KEY (network, chain_id, protocol, market_id, loop_key)
            REFERENCES v4_closed_loops (network, chain_id, protocol, market_id, loop_key),
        FOREIGN KEY (network, chain_id, protocol, pool_id)
            REFERENCES v4_exchange_pools (network, chain_id, protocol, pool_id),
        FOREIGN KEY (network, chain_id, protocol, base_pool_id)
            REFERENCES v4_exchange_pools (network, chain_id, protocol, pool_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS v4_closed_loop_optimizations (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL DEFAULT 'uniswap_v4',
        market_id TEXT NOT NULL,
        loop_key TEXT NOT NULL,
        hop_index INTEGER NOT NULL,
        optimization_id TEXT NOT NULL,
        original_pool_id TEXT,
        optimized_pool_id TEXT NOT NULL,
        optimization_basis TEXT,
        expected_price_improvement REAL,
        status TEXT NOT NULL DEFAULT 'temporary',
        created_at TEXT NOT NULL,
        expires_at TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, market_id, loop_key, hop_index, optimization_id),
        FOREIGN KEY (network, chain_id, protocol, market_id, loop_key, hop_index)
            REFERENCES v4_closed_loop_hops (
                network, chain_id, protocol, market_id, loop_key, hop_index
            ),
        FOREIGN KEY (network, chain_id, protocol, optimized_pool_id)
            REFERENCES v4_exchange_pools (network, chain_id, protocol, pool_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dex_candidate_anchor_tokens (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        token_role TEXT NOT NULL DEFAULT 'yc_zc',
        token_address TEXT NOT NULL,
        pool_count INTEGER NOT NULL DEFAULT 0,
        has_yc_optimal_pool INTEGER NOT NULL DEFAULT 0,
        has_zc_optimal_pool INTEGER NOT NULL DEFAULT 0,
        yc_optimal_pool_id TEXT,
        zc_optimal_pool_id TEXT,
        yc_zc_same_set INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, token_role, token_address),
        FOREIGN KEY (network, chain_id, token_address)
            REFERENCES chain_tokens (network, chain_id, token_address)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dex_candidate_pool_pairs (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        anchor_address TEXT NOT NULL,
        counter_address TEXT NOT NULL,
        address_pair TEXT NOT NULL,
        pool_count INTEGER NOT NULL DEFAULT 0,
        fixed_fee_liquid_pool_count INTEGER NOT NULL DEFAULT 0,
        priced_pool_count INTEGER NOT NULL DEFAULT 0,
        yc_optimal_pool_id TEXT,
        zc_optimal_pool_id TEXT,
        price_reference_json TEXT,
        price_outlier_filter TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair),
        FOREIGN KEY (network, chain_id, anchor_address)
            REFERENCES chain_tokens (network, chain_id, token_address),
        FOREIGN KEY (network, chain_id, counter_address)
            REFERENCES chain_tokens (network, chain_id, token_address)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dex_candidate_pools (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        address_pair TEXT NOT NULL,
        pool_id TEXT NOT NULL,
        pool_key_json TEXT,
        pool_manager TEXT,
        token0 TEXT,
        token1 TEXT,
        fee INTEGER,
        fee_label TEXT,
        dynamic_fee INTEGER NOT NULL DEFAULT 0,
        special_fee INTEGER NOT NULL DEFAULT 0,
        tick_spacing INTEGER,
        hooks TEXT,
        sqrt_price_x96 TEXT,
        tick INTEGER,
        has_liquidity INTEGER,
        liquidity TEXT,
        liquidity_index REAL,
        estimated_tvl_usd REAL,
        balances_json TEXT,
        depth_status TEXT,
        depth_band_json TEXT,
        usable_for_observation INTEGER NOT NULL DEFAULT 0,
        is_first_hop_recommended INTEGER NOT NULL DEFAULT 0,
        is_third_hop_recommended INTEGER NOT NULL DEFAULT 0,
        hop_recommendation_json TEXT,
        optimal_roles_json TEXT,
        liquidity_read_at TEXT,
        liquidity_block_number INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair, pool_id),
        FOREIGN KEY (network, chain_id, token0)
            REFERENCES chain_tokens (network, chain_id, token_address),
        FOREIGN KEY (network, chain_id, token1)
            REFERENCES chain_tokens (network, chain_id, token_address)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dex_candidate_pool_recommendations (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        counter_address TEXT NOT NULL,
        hop TEXT NOT NULL,
        rank INTEGER NOT NULL,
        pool_id TEXT NOT NULL,
        depth_score REAL,
        price REAL,
        fee INTEGER,
        selection_basis TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, counter_address, hop, pool_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_observation_yz_pairs (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        direction_key TEXT NOT NULL,
        token_x_address TEXT NOT NULL,
        token_y_address TEXT NOT NULL,
        address_pair TEXT NOT NULL,
        has_direct_swap_pool INTEGER NOT NULL DEFAULT 0,
        direct_swap_pool_count INTEGER NOT NULL DEFAULT 0,
        pool_ids_json TEXT NOT NULL DEFAULT '[]',
        observation_empty_reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key),
        FOREIGN KEY (network, chain_id, token_x_address)
            REFERENCES chain_tokens (network, chain_id, token_address),
        FOREIGN KEY (network, chain_id, token_y_address)
            REFERENCES chain_tokens (network, chain_id, token_address)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_observation_intermediate_pools (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        pool_id TEXT NOT NULL,
        pool_identity_mode TEXT NOT NULL DEFAULT 'v4_pool_id',
        pool_manager TEXT,
        pool_key_json TEXT,
        token0 TEXT,
        token1 TEXT,
        fee INTEGER,
        fee_label TEXT,
        dynamic_fee INTEGER NOT NULL DEFAULT 0,
        special_fee INTEGER NOT NULL DEFAULT 0,
        tick_spacing INTEGER,
        hooks TEXT,
        address_pair TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id),
        FOREIGN KEY (network, chain_id, token0)
            REFERENCES chain_tokens (network, chain_id, token_address),
        FOREIGN KEY (network, chain_id, token1)
            REFERENCES chain_tokens (network, chain_id, token_address)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_observation_liquidity_snapshots (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        pool_id TEXT NOT NULL,
        has_liquidity INTEGER,
        liquidity TEXT,
        liquidity_index REAL,
        estimated_tvl_usd REAL,
        balances_json TEXT,
        depth_status TEXT,
        depth_band_json TEXT,
        sqrt_price_x96 TEXT,
        tick INTEGER,
        liquidity_read_at TEXT,
        liquidity_block_number INTEGER,
        recent_swap_active INTEGER,
        observed_at TEXT NOT NULL,
        refreshed_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
    )
    """,
)

BUSINESS_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_v4_pools_token_edge ON v4_exchange_pools(network, chain_id, protocol, token0, token1)",
    "CREATE INDEX IF NOT EXISTS idx_v4_liquidity_pool ON v4_exchange_pool_liquidity_snapshots(network, chain_id, protocol, pool_id)",
    "CREATE INDEX IF NOT EXISTS idx_candidate_pairs_counter ON dex_candidate_pool_pairs(network, chain_id, protocol, anchor_symbol, counter_address)",
    "CREATE INDEX IF NOT EXISTS idx_candidate_pools_edge ON dex_candidate_pools(network, chain_id, protocol, anchor_symbol, address_pair)",
    "CREATE INDEX IF NOT EXISTS idx_observation_yz ON market_observation_yz_pairs(network, chain_id, protocol, anchor_symbol, token_x_address, token_y_address)",
    "CREATE INDEX IF NOT EXISTS idx_observation_pools ON market_observation_intermediate_pools(network, chain_id, protocol, anchor_symbol, pool_id)",
    "CREATE INDEX IF NOT EXISTS idx_closed_loop_anchor ON v4_closed_loops(network, chain_id, protocol, market_id, anchor_symbol)",
    "CREATE INDEX IF NOT EXISTS idx_closed_loop_hops_pool ON v4_closed_loop_hops(network, chain_id, protocol, market_id, pool_id)",
)


def ensure_business_schema(database_url: str) -> None:
    from core.local_kv import ensure_store, store_path

    path = store_path(database_url)
    ensure_store(path)
    with sqlite3.connect(str(path)) as connection:
        for statement in BUSINESS_DDL:
            connection.execute(statement)
        for statement in BUSINESS_INDEXES:
            connection.execute(statement)
        connection.execute(
            "PRAGMA journal_mode=WAL"
        )
        connection.commit()


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else "null"


def json_text(value: Any) -> str:
    return _json_dumps(value)
