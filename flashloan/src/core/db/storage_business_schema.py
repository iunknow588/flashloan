"""SQLite business tables for chain, V4 exchange, candidate, observation and loop data.

Schema ownership stays in Python: this module creates and migrates the
independent business tables. The Rust core only reads them through
``SQLITE_OPEN_READONLY`` and never executes DDL/DML.
"""

from __future__ import annotations

import sqlite3
import json
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
        yc_backup_pool_ids_json TEXT NOT NULL DEFAULT '[]',
        zc_backup_pool_ids_json TEXT NOT NULL DEFAULT '[]',
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
        direction_key TEXT NOT NULL,
        pool_count INTEGER NOT NULL DEFAULT 0,
        fixed_fee_liquid_pool_count INTEGER NOT NULL DEFAULT 0,
        priced_pool_count INTEGER NOT NULL DEFAULT 0,
        yc_optimal_pool_id TEXT,
        zc_optimal_pool_id TEXT,
        yc_backup_pool_ids_json TEXT NOT NULL DEFAULT '[]',
        zc_backup_pool_ids_json TEXT NOT NULL DEFAULT '[]',
        price_reference_json TEXT,
        price_outlier_filter TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key),
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
        direction_key TEXT NOT NULL,
        direction_address_pair TEXT,
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
        usable_for_observation INTEGER,
        is_first_hop_recommended INTEGER NOT NULL DEFAULT 0,
        is_third_hop_recommended INTEGER NOT NULL DEFAULT 0,
        hop_recommendation_json TEXT,
        optimal_roles_json TEXT,
        liquidity_read_at TEXT,
        liquidity_block_number INTEGER,
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
    CREATE TABLE IF NOT EXISTS dex_candidate_pool_recommendations (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        counter_address TEXT NOT NULL,
        direction_key TEXT NOT NULL,
        hop TEXT NOT NULL,
        rank INTEGER NOT NULL,
        pool_id TEXT NOT NULL,
        depth_score REAL,
        price REAL,
        fee INTEGER,
        selection_basis TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, counter_address, direction_key, hop, pool_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dex_candidate_abnormal_anchor_tokens (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        token_role TEXT NOT NULL DEFAULT 'yc_zc_abnormal',
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
    CREATE TABLE IF NOT EXISTS dex_candidate_abnormal_pool_pairs (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        anchor_address TEXT NOT NULL,
        counter_address TEXT NOT NULL,
        address_pair TEXT NOT NULL,
        direction_key TEXT NOT NULL,
        pool_count INTEGER NOT NULL DEFAULT 0,
        fixed_fee_liquid_pool_count INTEGER NOT NULL DEFAULT 0,
        priced_pool_count INTEGER NOT NULL DEFAULT 0,
        yc_optimal_pool_id TEXT,
        zc_optimal_pool_id TEXT,
        price_reference_json TEXT,
        price_outlier_filter TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key),
        FOREIGN KEY (network, chain_id, anchor_address)
            REFERENCES chain_tokens (network, chain_id, token_address),
        FOREIGN KEY (network, chain_id, counter_address)
            REFERENCES chain_tokens (network, chain_id, token_address)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dex_candidate_abnormal_pools (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        address_pair TEXT NOT NULL,
        direction_key TEXT NOT NULL,
        direction_address_pair TEXT,
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
        usable_for_observation INTEGER,
        is_first_hop_recommended INTEGER NOT NULL DEFAULT 0,
        is_third_hop_recommended INTEGER NOT NULL DEFAULT 0,
        hop_recommendation_json TEXT,
        optimal_roles_json TEXT,
        liquidity_read_at TEXT,
        liquidity_block_number INTEGER,
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
        optimal_pool_id TEXT,
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
    CREATE TABLE IF NOT EXISTS market_observation_abnormal_yz_pairs (
        network TEXT NOT NULL,
        chain_id INTEGER NOT NULL,
        protocol TEXT NOT NULL,
        anchor_scope TEXT NOT NULL,
        anchor_symbol TEXT NOT NULL,
        direction_key TEXT NOT NULL,
        token_x_address TEXT NOT NULL,
        token_y_address TEXT NOT NULL,
        address_pair TEXT NOT NULL,
        has_non_normal_swap_pool INTEGER NOT NULL DEFAULT 0,
        non_normal_swap_pool_count INTEGER NOT NULL DEFAULT 0,
        pool_ids_json TEXT NOT NULL DEFAULT '[]',
        optimal_pool_id TEXT,
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
    CREATE TABLE IF NOT EXISTS market_observation_abnormal_intermediate_pools (
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
    "CREATE INDEX IF NOT EXISTS idx_candidate_pairs_direction ON dex_candidate_pool_pairs(network, chain_id, protocol, anchor_symbol, direction_key)",
    "CREATE INDEX IF NOT EXISTS idx_candidate_pools_edge ON dex_candidate_pools(network, chain_id, protocol, anchor_symbol, address_pair)",
    "CREATE INDEX IF NOT EXISTS idx_candidate_pools_direction ON dex_candidate_pools(network, chain_id, protocol, anchor_symbol, direction_key)",
    "CREATE INDEX IF NOT EXISTS idx_candidate_recommendations_direction ON dex_candidate_pool_recommendations(network, chain_id, protocol, anchor_symbol, direction_key)",
    "CREATE INDEX IF NOT EXISTS idx_observation_yz ON market_observation_yz_pairs(network, chain_id, protocol, anchor_symbol, token_x_address, token_y_address)",
    "CREATE INDEX IF NOT EXISTS idx_observation_yz_direction ON market_observation_yz_pairs(network, chain_id, protocol, anchor_symbol, direction_key)",
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
        connection.execute(
            "PRAGMA journal_mode=WAL"
        )
        connection.commit()
    migrate_business_direction_schema(database_url)
    with sqlite3.connect(str(path)) as connection:
        for statement in BUSINESS_INDEXES:
            connection.execute(statement)
        connection.commit()


def _record_business_schema_version(path: str, version: str) -> None:
    from core.local_kv import meta_put

    meta_put(path, "business_schema_version", version)


def migrate_business_direction_schema(database_url: str) -> dict[str, int]:
    """Migrate old unordered candidate/observation tables to directed rows.

    Existing market-observation direction keys already distinguish Y:Z from
    Z:Y.  Candidate pair/pool/recommendation rows are duplicated and re-keyed
    by ``direction_key`` while physical ``pool_id`` rows stay unique by anchor.
    Old canonical rows are removed only after their directed replacement rows
    exist, so the migration is safe to run repeatedly.
    """

    from core.local_kv import store_path

    path = str(store_path(database_url))
    counts = {
        "candidate_pairs": 0,
        "candidate_pools": 0,
        "recommendations": 0,
        "abnormal_pairs": 0,
        "abnormal_pools": 0,
        "observation_pairs": 0,
        "abnormal_observation_pairs": 0,
    }
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.row_factory = lambda cursor, row: {
            description[0]: value
            for description, value in zip(cursor.description or (), row)
        }
        _record_business_schema_version(path, "directional_pairs_v1")

        def _column_names(table: str) -> set[str]:
            return {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM pragma_table_info(?)",
                    (table,),
                ).fetchall()
            }

        def _ensure_column(table: str, column: str, definition: str) -> None:
            if column not in _column_names(table):
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )

        for table, columns in {
            "dex_candidate_anchor_tokens": {
                "yc_backup_pool_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "zc_backup_pool_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            },
            "dex_candidate_abnormal_anchor_tokens": {
                "yc_backup_pool_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "zc_backup_pool_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            },
            "dex_candidate_pool_pairs": {
                "direction_key": "TEXT",
                "yc_backup_pool_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "zc_backup_pool_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            },
            "dex_candidate_abnormal_pool_pairs": {
                "direction_key": "TEXT",
                "yc_backup_pool_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "zc_backup_pool_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            },
            "dex_candidate_pools": {
                "direction_key": "TEXT",
                "direction_address_pair": "TEXT",
            },
            "dex_candidate_pool_recommendations": {"direction_key": "TEXT"},
            "dex_candidate_abnormal_pools": {
                "direction_key": "TEXT",
                "direction_address_pair": "TEXT",
            },
            "market_observation_yz_pairs": {"optimal_pool_id": "TEXT"},
            "market_observation_abnormal_yz_pairs": {"optimal_pool_id": "TEXT"},
        }.items():
            for column, definition in columns.items():
                _ensure_column(table, column, definition)
        pair_rows = connection.execute(
            """
            SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
                   anchor_address, counter_address, address_pair, pool_count,
                   fixed_fee_liquid_pool_count, priced_pool_count,
                   yc_optimal_pool_id, zc_optimal_pool_id, price_reference_json,
                   price_outlier_filter, created_at, updated_at
            FROM dex_candidate_pool_pairs
            WHERE direction_key IS NULL OR direction_key = ''
               OR address_pair != direction_key
            ORDER BY anchor_symbol, anchor_address
            """
        ).fetchall()
        pool_rows = connection.execute(
            """
            SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
                   address_pair, pool_id, pool_key_json, pool_manager, token0,
                   token1, fee, fee_label, dynamic_fee, special_fee, tick_spacing,
                   hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
                   liquidity_index, estimated_tvl_usd, balances_json, depth_status,
                   depth_band_json, usable_for_observation, is_first_hop_recommended,
                   is_third_hop_recommended, hop_recommendation_json,
                   optimal_roles_json, liquidity_read_at, liquidity_block_number,
                   created_at, updated_at
            FROM dex_candidate_pools
            WHERE direction_key IS NULL OR direction_key = ''
               OR address_pair != direction_key
               OR direction_address_pair IS NULL
               OR direction_address_pair != direction_key
            ORDER BY anchor_symbol, address_pair, pool_id
            """
        ).fetchall()
        recommendation_rows = connection.execute(
            """
            SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
                   counter_address, hop, rank, pool_id, depth_score, price, fee,
                   selection_basis, created_at, updated_at
            FROM dex_candidate_pool_recommendations
            WHERE direction_key IS NULL OR direction_key = ''
            ORDER BY anchor_symbol, counter_address, hop, rank
            """
        ).fetchall()
        for row in pair_rows:
            anchor = str(row.get("anchor_address") or "").lower()
            counter = str(row.get("counter_address") or "").lower()
            if not anchor or not counter:
                continue
            for direction_key, direction_anchor, direction_counter in (
                (f"{anchor}:{counter}", anchor, counter),
                (f"{counter}:{anchor}", counter, anchor),
            ):
                forward_first = direction_key == f"{anchor}:{counter}"
                connection.execute(
                    """
                    INSERT OR REPLACE INTO dex_candidate_pool_pairs (
                        network, chain_id, protocol, anchor_scope, anchor_symbol,
                        anchor_address, counter_address, address_pair,
                        direction_key, pool_count,
                        fixed_fee_liquid_pool_count, priced_pool_count,
                        yc_optimal_pool_id, zc_optimal_pool_id,
                        price_reference_json, price_outlier_filter,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["network"],
                        row["chain_id"],
                        row["protocol"],
                        row["anchor_scope"],
                        row["anchor_symbol"],
                        direction_anchor,
                        direction_counter,
                        direction_key,
                        direction_key,
                        row["pool_count"],
                        row["fixed_fee_liquid_pool_count"],
                        row["priced_pool_count"],
                        row["yc_optimal_pool_id"] if forward_first else "",
                        "" if forward_first else row["zc_optimal_pool_id"],
                        row["price_reference_json"],
                        row["price_outlier_filter"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
                counts["candidate_pairs"] += 1
        # Physical pools stay unique by anchor+pool_id.  The physical row uses
        # the fixed token0:token1 address_pair as a display-only canonical key;
        # direction-specific ordering lives in pair/recommendation/observation
        # tables, not in duplicate pool rows.
        for row in pool_rows:
            token0 = str(row.get("token0") or "").lower()
            token1 = str(row.get("token1") or "").lower()
            if not token0 or not token1:
                continue
            direction_pair = f"{token0}:{token1}"
            direction_key = direction_pair
            connection.execute(
                """
                INSERT OR REPLACE INTO dex_candidate_pools (
                    network, chain_id, protocol, anchor_scope, anchor_symbol,
                    address_pair, direction_key, direction_address_pair,
                    pool_id, pool_key_json, pool_manager, token0, token1,
                    fee, fee_label, dynamic_fee, special_fee, tick_spacing,
                    hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
                    liquidity_index, estimated_tvl_usd, balances_json,
                    depth_status, depth_band_json, usable_for_observation,
                    is_first_hop_recommended, is_third_hop_recommended,
                    hop_recommendation_json, optimal_roles_json,
                    liquidity_read_at, liquidity_block_number,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["network"],
                    row["chain_id"],
                    row["protocol"],
                    row["anchor_scope"],
                    row["anchor_symbol"],
                    direction_key,
                    direction_key,
                    direction_key,
                    row["pool_id"],
                    row["pool_key_json"],
                    row["pool_manager"],
                    row["token0"],
                    row["token1"],
                    row["fee"],
                    row["fee_label"],
                    row["dynamic_fee"],
                    row["special_fee"],
                    row["tick_spacing"],
                    row["hooks"],
                    row["sqrt_price_x96"],
                    row["tick"],
                    row["has_liquidity"],
                    row["liquidity"],
                    row["liquidity_index"],
                    row["estimated_tvl_usd"],
                    row["balances_json"],
                    row["depth_status"],
                    row["depth_band_json"],
                    row["usable_for_observation"],
                    row["is_first_hop_recommended"],
                    row["is_third_hop_recommended"],
                    row["hop_recommendation_json"],
                    row["optimal_roles_json"],
                    row["liquidity_read_at"],
                    row["liquidity_block_number"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
            counts["candidate_pools"] += 1
        for row in recommendation_rows:
            anchor_row = connection.execute(
                """
                SELECT anchor_address FROM dex_candidate_pool_pairs
                WHERE network = ? AND chain_id = ? AND protocol = ?
                  AND anchor_scope = ? AND anchor_symbol = ?
                ORDER BY anchor_address LIMIT 1
                """,
                (
                    row["network"],
                    row["chain_id"],
                    row["protocol"],
                    row["anchor_scope"],
                    row["anchor_symbol"],
                ),
            ).fetchone()
            if not anchor_row:
                continue
            anchor_address = str(anchor_row["anchor_address"] or "").lower()
            counter = str(row.get("counter_address") or "").lower()
            if not anchor_address:
                continue
            if str(row.get("hop") or "").lower() == "third":
                direction_key = f"{counter}:{anchor_address}"
            else:
                direction_key = f"{anchor_address}:{counter}"
            connection.execute(
                """
                UPDATE dex_candidate_pool_recommendations
                SET direction_key = ?
                WHERE network = ? AND chain_id = ? AND protocol = ?
                  AND anchor_scope = ? AND anchor_symbol = ?
                  AND counter_address = ? AND hop = ? AND pool_id = ?
                """,
                (
                    direction_key,
                    row["network"],
                    row["chain_id"],
                    row["protocol"],
                    row["anchor_scope"],
                    row["anchor_symbol"],
                    row["counter_address"],
                    row["hop"],
                    row["pool_id"],
                ),
            )
            counts["recommendations"] += 1
        connection.execute(
            """
            DELETE FROM dex_candidate_pool_recommendations
            WHERE rowid NOT IN (
                SELECT MIN(rowid)
                FROM dex_candidate_pool_recommendations
                GROUP BY network, chain_id, protocol, anchor_scope, anchor_symbol,
                         counter_address, direction_key, hop
            )
            """
        )
        # Abnormal tables use the same directional key semantics.
        abnormal_pair_rows = connection.execute(
            """
            SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
                   anchor_address, counter_address, address_pair, pool_count,
                   fixed_fee_liquid_pool_count, priced_pool_count,
                   yc_optimal_pool_id, zc_optimal_pool_id, price_reference_json,
                   price_outlier_filter, created_at, updated_at
            FROM dex_candidate_abnormal_pool_pairs
            WHERE direction_key IS NULL OR direction_key = ''
               OR address_pair != direction_key
            ORDER BY anchor_symbol, anchor_address
            """
        ).fetchall()
        abnormal_pool_rows = connection.execute(
            """
            SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
                   address_pair, pool_id, pool_key_json, pool_manager, token0,
                   token1, fee, fee_label, dynamic_fee, special_fee, tick_spacing,
                   hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
                   liquidity_index, estimated_tvl_usd, balances_json, depth_status,
                   depth_band_json, usable_for_observation, is_first_hop_recommended,
                   is_third_hop_recommended, hop_recommendation_json,
                   optimal_roles_json, liquidity_read_at, liquidity_block_number,
                   created_at, updated_at
            FROM dex_candidate_abnormal_pools
            WHERE direction_key IS NULL OR direction_key = ''
               OR address_pair != direction_key
               OR direction_address_pair IS NULL
               OR direction_address_pair != direction_key
            ORDER BY anchor_symbol, address_pair, pool_id
            """
        ).fetchall()
        for row in abnormal_pair_rows:
            anchor = str(row.get("anchor_address") or "").lower()
            counter = str(row.get("counter_address") or "").lower()
            if not anchor or not counter:
                continue
            for direction_key, direction_anchor, direction_counter in (
                (f"{anchor}:{counter}", anchor, counter),
                (f"{counter}:{anchor}", counter, anchor),
            ):
                forward_first = direction_key == f"{anchor}:{counter}"
                connection.execute(
                    """
                    INSERT OR REPLACE INTO dex_candidate_abnormal_pool_pairs (
                        network, chain_id, protocol, anchor_scope, anchor_symbol,
                        anchor_address, counter_address, address_pair,
                        direction_key, pool_count,
                        fixed_fee_liquid_pool_count, priced_pool_count,
                        yc_optimal_pool_id, zc_optimal_pool_id,
                        price_reference_json, price_outlier_filter,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["network"],
                        row["chain_id"],
                        row["protocol"],
                        row["anchor_scope"],
                        row["anchor_symbol"],
                        direction_anchor,
                        direction_counter,
                        direction_key,
                        direction_key,
                        row["pool_count"],
                        row["fixed_fee_liquid_pool_count"],
                        row["priced_pool_count"],
                        row["yc_optimal_pool_id"] if forward_first else "",
                        "" if forward_first else row["zc_optimal_pool_id"],
                        row["price_reference_json"],
                        row["price_outlier_filter"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
                counts["abnormal_pairs"] += 1
        for row in abnormal_pool_rows:
            token0 = str(row.get("token0") or "").lower()
            token1 = str(row.get("token1") or "").lower()
            if not token0 or not token1:
                continue
            direction_pair = f"{token0}:{token1}"
            connection.execute(
                """
                INSERT OR REPLACE INTO dex_candidate_abnormal_pools (
                    network, chain_id, protocol, anchor_scope, anchor_symbol,
                    address_pair, direction_key, direction_address_pair,
                    pool_id, pool_key_json, pool_manager, token0, token1,
                    fee, fee_label, dynamic_fee, special_fee, tick_spacing,
                    hooks, sqrt_price_x96, tick, has_liquidity, liquidity,
                    liquidity_index, estimated_tvl_usd, balances_json,
                    depth_status, depth_band_json, usable_for_observation,
                    is_first_hop_recommended, is_third_hop_recommended,
                    hop_recommendation_json, optimal_roles_json,
                    liquidity_read_at, liquidity_block_number,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["network"],
                    row["chain_id"],
                    row["protocol"],
                    row["anchor_scope"],
                    row["anchor_symbol"],
                    direction_pair,
                    direction_pair,
                    direction_pair,
                    row["pool_id"],
                    row["pool_key_json"],
                    row["pool_manager"],
                    row["token0"],
                    row["token1"],
                    row["fee"],
                    row["fee_label"],
                    row["dynamic_fee"],
                    row["special_fee"],
                    row["tick_spacing"],
                    row["hooks"],
                    row["sqrt_price_x96"],
                    row["tick"],
                    row["has_liquidity"],
                    row["liquidity"],
                    row["liquidity_index"],
                    row["estimated_tvl_usd"],
                    row["balances_json"],
                    row["depth_status"],
                    row["depth_band_json"],
                    row["usable_for_observation"],
                    row["is_first_hop_recommended"],
                    row["is_third_hop_recommended"],
                    row["hop_recommendation_json"],
                    row["optimal_roles_json"],
                    row["liquidity_read_at"],
                    row["liquidity_block_number"],
                    row["created_at"],
                    row["updated_at"],
                ),
            )
            counts["abnormal_pools"] += 1
        # Directed replacement rows now exist; remove canonical/orphan rows so
        # every remaining row has a valid ``direction_key`` and ordered edge.
        for table in (
            "dex_candidate_pool_pairs",
            "dex_candidate_abnormal_pool_pairs",
        ):
            connection.execute(
                f"""
                DELETE FROM {table}
                WHERE direction_key IS NULL OR direction_key = ''
                   OR address_pair != direction_key
                """
            )
        for table in (
            "dex_candidate_pools",
            "dex_candidate_abnormal_pools",
        ):
            connection.execute(
                f"""
                DELETE FROM {table}
                WHERE direction_key IS NULL OR direction_key = ''
                   OR address_pair != direction_key
                   OR direction_address_pair IS NULL
                   OR direction_address_pair != direction_key
                """
            )
        connection.execute(
            """
            DELETE FROM dex_candidate_pool_recommendations
            WHERE direction_key IS NULL OR direction_key = ''
            """
        )

        # Recompute first/third flags on the physical pool row as a union over
        # both directed recommendation keys.  Pool rows are physical, so a pool
        # that is best for X->A and a different best for A->X still has one row
        # with both flags; the directed recommendation rows keep the choice.
        def _recommendation_exists(
            *,
            direction_key: str,
            hop: str,
            network: object,
            chain_id: object,
            protocol: object,
            anchor_scope: object,
            anchor_symbol: object,
            pool_id: object,
        ) -> bool:
            return bool(
                connection.execute(
                    """
                    SELECT 1 FROM dex_candidate_pool_recommendations
                    WHERE network = ? AND chain_id = ? AND protocol = ?
                      AND anchor_scope = ? AND anchor_symbol = ?
                      AND direction_key = ? AND hop = ? AND pool_id = ?
                    LIMIT 1
                    """,
                    (
                        network,
                        chain_id,
                        protocol,
                        anchor_scope,
                        anchor_symbol,
                        direction_key,
                        hop,
                        pool_id,
                    ),
                ).fetchone()
            )

        # Abnormal pool rows have no separate recommendation table, so only
        # the normal candidate pools are re-keyed against directed recs.
        for table in ("dex_candidate_pools",):
            rows = connection.execute(
                f"""
                SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
                       direction_key, token0, token1, pool_id
                FROM {table}
                ORDER BY anchor_symbol, direction_key, pool_id
                """
            ).fetchall()
            for row in rows:
                token0 = str(row.get("token0") or "").lower()
                token1 = str(row.get("token1") or "").lower()
                direction_keys = {str(row.get("direction_key") or "")}
                if token0 and token1:
                    direction_keys.add(f"{token0}:{token1}")
                    direction_keys.add(f"{token1}:{token0}")
                direction_keys.discard("")
                canonical_direction = f"{token0}:{token1}" if token0 and token1 else (direction_keys.pop() if direction_keys else "")
                first_exists = any(
                    _recommendation_exists(
                        direction_key=direction_key,
                        hop="first",
                        network=row["network"],
                        chain_id=row["chain_id"],
                        protocol=row["protocol"],
                        anchor_scope=row["anchor_scope"],
                        anchor_symbol=row["anchor_symbol"],
                        pool_id=row["pool_id"],
                    )
                    for direction_key in direction_keys
                )
                third_exists = any(
                    _recommendation_exists(
                        direction_key=direction_key,
                        hop="third",
                        network=row["network"],
                        chain_id=row["chain_id"],
                        protocol=row["protocol"],
                        anchor_scope=row["anchor_scope"],
                        anchor_symbol=row["anchor_symbol"],
                        pool_id=row["pool_id"],
                    )
                    for direction_key in direction_keys
                )
                connection.execute(
                    f"""
                    UPDATE {table}
                    SET address_pair = ?,
                        direction_key = ?,
                        direction_address_pair = ?,
                        is_first_hop_recommended = ?,
                        is_third_hop_recommended = ?,
                        hop_recommendation_json = ?,
                        optimal_roles_json = ?
                    WHERE network = ? AND chain_id = ? AND protocol = ?
                      AND anchor_scope = ? AND anchor_symbol = ?
                      AND pool_id = ?
                    """,
                    (
                        canonical_direction,
                        canonical_direction,
                        canonical_direction,
                        int(first_exists),
                        int(third_exists),
                        json.dumps(
                            {"first": bool(first_exists), "third": bool(third_exists)},
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            {"yc": bool(first_exists), "zc": bool(third_exists)},
                            separators=(",", ":"),
                        ),
                        row["network"],
                        row["chain_id"],
                        row["protocol"],
                        row["anchor_scope"],
                        row["anchor_symbol"],
                        row["pool_id"],
                    ),
                )
        def _pair_table_ddl(name: str, *, with_backup_columns: bool = False) -> str:
            backup_columns = ""
            if with_backup_columns:
                backup_columns = """
                yc_backup_pool_ids_json TEXT NOT NULL DEFAULT '[]',
                zc_backup_pool_ids_json TEXT NOT NULL DEFAULT '[]',
                """
            return f"""
            CREATE TABLE {name} (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                protocol TEXT NOT NULL,
                anchor_scope TEXT NOT NULL,
                anchor_symbol TEXT NOT NULL,
                anchor_address TEXT NOT NULL,
                counter_address TEXT NOT NULL,
                address_pair TEXT NOT NULL,
                direction_key TEXT NOT NULL,
                pool_count INTEGER NOT NULL DEFAULT 0,
                fixed_fee_liquid_pool_count INTEGER NOT NULL DEFAULT 0,
                priced_pool_count INTEGER NOT NULL DEFAULT 0,
                yc_optimal_pool_id TEXT,
                zc_optimal_pool_id TEXT,
                {backup_columns}
                price_reference_json TEXT,
                price_outlier_filter TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key),
                FOREIGN KEY (network, chain_id, anchor_address)
                    REFERENCES chain_tokens (network, chain_id, token_address),
                FOREIGN KEY (network, chain_id, counter_address)
                    REFERENCES chain_tokens (network, chain_id, token_address)
            )
            """

        def _pool_table_ddl(name: str) -> str:
            return f"""
            CREATE TABLE {name} (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                protocol TEXT NOT NULL,
                anchor_scope TEXT NOT NULL,
                anchor_symbol TEXT NOT NULL,
                address_pair TEXT NOT NULL,
                direction_key TEXT NOT NULL,
                direction_address_pair TEXT,
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
                usable_for_observation INTEGER,
                is_first_hop_recommended INTEGER NOT NULL DEFAULT 0,
                is_third_hop_recommended INTEGER NOT NULL DEFAULT 0,
                hop_recommendation_json TEXT,
                optimal_roles_json TEXT,
                liquidity_read_at TEXT,
                liquidity_block_number INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
            )
            """

        def _recommendation_table_ddl(name: str) -> str:
            return f"""
            CREATE TABLE {name} (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                protocol TEXT NOT NULL,
                anchor_scope TEXT NOT NULL,
                anchor_symbol TEXT NOT NULL,
                counter_address TEXT NOT NULL,
                direction_key TEXT NOT NULL,
                hop TEXT NOT NULL,
                rank INTEGER NOT NULL,
                pool_id TEXT NOT NULL,
                depth_score REAL,
                price REAL,
                fee INTEGER,
                selection_basis TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, counter_address, direction_key, hop, pool_id)
            )
            """

        def _primary_key_columns(table: str) -> list[str]:
            rows = connection.execute(
                """
                SELECT name FROM pragma_table_info(?)
                WHERE pk > 0 ORDER BY pk
                """,
                (table,),
            ).fetchall()
            return [str(row["name"]) for row in rows]

        pair_columns_with_backup = """
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            anchor_address, counter_address, address_pair, direction_key,
            pool_count, fixed_fee_liquid_pool_count, priced_pool_count,
            yc_optimal_pool_id, zc_optimal_pool_id,
            yc_backup_pool_ids_json, zc_backup_pool_ids_json,
            price_reference_json, price_outlier_filter, created_at, updated_at
        """
        for table in (
            "dex_candidate_pool_pairs",
            "dex_candidate_abnormal_pool_pairs",
        ):
            if "direction_key" in _primary_key_columns(table):
                continue
            temp = f"{table}_directional_v1"
            connection.execute(f"DROP TABLE IF EXISTS {temp}")
            connection.execute(
                _pair_table_ddl(temp, with_backup_columns=True)
            )
            columns = pair_columns_with_backup
            connection.execute(
                f"INSERT INTO {temp} ({columns}) SELECT {columns} FROM {table}"
            )
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE {temp} RENAME TO {table}")

        pool_columns = """
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            address_pair, direction_key, direction_address_pair, pool_id,
            pool_key_json, pool_manager, token0, token1, fee, fee_label,
            dynamic_fee, special_fee, tick_spacing, hooks, sqrt_price_x96,
            tick, has_liquidity, liquidity, liquidity_index, estimated_tvl_usd,
            balances_json, depth_status, depth_band_json,
            usable_for_observation, is_first_hop_recommended,
            is_third_hop_recommended, hop_recommendation_json,
            optimal_roles_json, liquidity_read_at, liquidity_block_number,
            created_at, updated_at
        """
        for table in ("dex_candidate_pools", "dex_candidate_abnormal_pools"):
            if "direction_key" in _primary_key_columns(table):
                continue
            temp = f"{table}_directional_v1"
            connection.execute(f"DROP TABLE IF EXISTS {temp}")
            connection.execute(_pool_table_ddl(temp))
            connection.execute(
                f"INSERT OR IGNORE INTO {temp} ({pool_columns}) SELECT {pool_columns} FROM {table}"
            )
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE {temp} RENAME TO {table}")
        recommendation_columns = """
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            counter_address, direction_key, hop, rank, pool_id, depth_score,
            price, fee, selection_basis, created_at, updated_at
        """
        if "direction_key" not in _primary_key_columns("dex_candidate_pool_recommendations"):
            temp = "dex_candidate_pool_recommendations_directional_v1"
            connection.execute(f"DROP TABLE IF EXISTS {temp}")
            connection.execute(_recommendation_table_ddl(temp))
            connection.execute(
                f"INSERT OR IGNORE INTO {temp} ({recommendation_columns}) "
                f"SELECT {recommendation_columns} FROM dex_candidate_pool_recommendations"
            )
            connection.execute("DROP TABLE dex_candidate_pool_recommendations")
            connection.execute(f"ALTER TABLE {temp} RENAME TO dex_candidate_pool_recommendations")
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_candidate_pairs_counter "
            "ON dex_candidate_pool_pairs(network, chain_id, protocol, anchor_symbol, counter_address)",
            "CREATE INDEX IF NOT EXISTS idx_candidate_pairs_direction "
            "ON dex_candidate_pool_pairs(network, chain_id, protocol, anchor_symbol, direction_key)",
            "CREATE INDEX IF NOT EXISTS idx_candidate_pools_direction "
            "ON dex_candidate_pools(network, chain_id, protocol, anchor_symbol, direction_key)",
            "CREATE INDEX IF NOT EXISTS idx_candidate_recommendations_direction "
            "ON dex_candidate_pool_recommendations(network, chain_id, protocol, anchor_symbol, direction_key)",
            "CREATE INDEX IF NOT EXISTS idx_observation_yz_direction "
            "ON market_observation_yz_pairs(network, chain_id, protocol, anchor_symbol, direction_key)",
        ):
            connection.execute(statement)

        # Market observation direction rows already exist as ordered pairs;
        # backfill the directional address_pair and optimal pool when missing.
        # Legacy rows are truncated to one pool here, while new observation
        # writes keep up to three directed pools (primary + backups).
        max_pools_per_direction = 1
        for table, has_column, pool_count_column in (
            ("market_observation_yz_pairs", "has_direct_swap_pool", "direct_swap_pool_count"),
            (
                "market_observation_abnormal_yz_pairs",
                "has_non_normal_swap_pool",
                "non_normal_swap_pool_count",
            ),
        ):
            rows = connection.execute(
                f"""
                SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
                       direction_key, token_x_address, token_y_address,
                       address_pair, pool_ids_json, optimal_pool_id
                FROM {table}
                ORDER BY anchor_symbol, direction_key
                """
            ).fetchall()
            updates = []
            for row in rows:
                token_x = str(row.get("token_x_address") or "").lower()
                token_y = str(row.get("token_y_address") or "").lower()
                if not token_x or not token_y:
                    continue
                direction_pair = f"{token_x}:{token_y}"
                original_pool_ids = json.loads(row.get("pool_ids_json") or "[]")
                if not isinstance(original_pool_ids, list):
                    original_pool_ids = []
                pool_ids = original_pool_ids[:max_pools_per_direction]
                optimal_pool = row.get("optimal_pool_id") or (pool_ids[0] if pool_ids else "")
                current_address_pair = str(row.get("address_pair") or "")
                current_optimal_pool = str(row.get("optimal_pool_id") or "")
                needs_update = (
                    current_address_pair != direction_pair
                    or current_optimal_pool != str(optimal_pool or "")
                    or original_pool_ids != pool_ids
                )
                if not needs_update:
                    continue
                updates.append(
                    (
                        direction_pair,
                        json.dumps(pool_ids, separators=(",", ":")),
                        optimal_pool,
                        int(bool(pool_ids)),
                        len(pool_ids),
                        row["network"],
                        row["chain_id"],
                        row["protocol"],
                        row["anchor_scope"],
                        row["anchor_symbol"],
                        row["direction_key"],
                    )
                )
            connection.executemany(
                f"""
                UPDATE {table}
                SET address_pair = ?, pool_ids_json = ?, optimal_pool_id = ?,
                    {has_column} = ?, {pool_count_column} = ?
                WHERE network = ? AND chain_id = ? AND protocol = ?
                  AND anchor_scope = ? AND anchor_symbol = ?
                  AND direction_key = ?
                """,
                updates,
            )
            counts["observation_pairs" if table == "market_observation_yz_pairs" else "abnormal_observation_pairs"] += len(updates)
        # Directional observations must be complete for every anchor: N
        # non-anchor tokens yield exactly N*(N-1) directed Y-Z pairs. The
        # assertion is the migration's integrity gate and also catches rows
        # that would otherwise survive as canonical/orphan pairs.
        integrity_errors: list[str] = []
        for row in connection.execute(
            """
            SELECT network, chain_id, protocol, anchor_scope, anchor_symbol,
                   COUNT(DISTINCT direction_key) AS direction_count
            FROM market_observation_yz_pairs
            GROUP BY network, chain_id, protocol, anchor_scope, anchor_symbol
            ORDER BY anchor_symbol
            """
        ).fetchall():
            token_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS token_count FROM dex_candidate_anchor_tokens
                    WHERE network = ? AND chain_id = ? AND protocol = ?
                      AND anchor_scope = ? AND anchor_symbol = ?
                    """,
                    (
                        row["network"],
                        row["chain_id"],
                        row["protocol"],
                        row["anchor_scope"],
                        row["anchor_symbol"],
                    ),
                ).fetchone()["token_count"]
            )
            direction_count = int(
                row["direction_count"]
            )
            expected = int(token_count) * (int(token_count) - 1)
            if direction_count != expected:
                integrity_errors.append(
                    f"{row['anchor_symbol']}: expected {expected} directions, found {direction_count}"
                )
        if integrity_errors:
            raise RuntimeError("directional migration incomplete: " + "; ".join(integrity_errors))

        connection.commit()
    return counts


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else "null"


def json_text(value: Any) -> str:
    return _json_dumps(value)
