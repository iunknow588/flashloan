"""Legacy-to-directional SQLite migration coverage for candidate/observation rows."""

from __future__ import annotations

import json
import sqlite3

from core.db.storage_business_schema import ensure_business_schema


NETWORK = "robinhood-testnet"
CHAIN_ID = 46630
PROTOCOL = "uniswap_v4"
ANCHOR_SCOPE = "dex-arbitrage-46630-uniswap-v4"
ANCHOR_SYMBOL = "TZ"
ANCHOR = "0xaaa"
Y = "0xbbb"
Z = "0xccc"
STAMP = "2026-10-12T00:00:00Z"


def _connect(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=OFF")
    return connection


def _create_legacy_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE dex_candidate_anchor_tokens (
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
            PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, token_role, token_address)
        );

        CREATE TABLE dex_candidate_pool_pairs (
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
            PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair)
        );

        CREATE TABLE dex_candidate_pools (
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
            PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
        );

        CREATE TABLE dex_candidate_pool_recommendations (
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
        );

        CREATE TABLE dex_candidate_abnormal_pool_pairs (
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
            PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, address_pair)
        );

        CREATE TABLE dex_candidate_abnormal_pools (
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
            PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, pool_id)
        );

        CREATE TABLE market_observation_yz_pairs (
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
            PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key)
        );

        CREATE TABLE market_observation_abnormal_yz_pairs (
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
            observation_empty_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (network, chain_id, protocol, anchor_scope, anchor_symbol, direction_key)
        );
        """
    )


def _seed_legacy_rows(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO dex_candidate_anchor_tokens (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            token_role, token_address, pool_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'yc_zc', ?, 0, ?, ?)
        """,
        (NETWORK, CHAIN_ID, PROTOCOL, ANCHOR_SCOPE, ANCHOR_SYMBOL, Y, STAMP, STAMP),
    )
    connection.execute(
        """
        INSERT INTO dex_candidate_anchor_tokens (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            token_role, token_address, pool_count, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'yc_zc', ?, 0, ?, ?)
        """,
        (NETWORK, CHAIN_ID, PROTOCOL, ANCHOR_SCOPE, ANCHOR_SYMBOL, Z, STAMP, STAMP),
    )
    common_pair = (
        NETWORK,
        CHAIN_ID,
        PROTOCOL,
        ANCHOR_SCOPE,
        ANCHOR_SYMBOL,
        ANCHOR,
        Y,
        f"{ANCHOR}:{Y}",
        2,
        2,
        2,
        "pool-yc",
        "pool-zc",
        "{}",
        "fixed_fee_no_hook_low_fee",
        STAMP,
        STAMP,
    )
    connection.execute(
        """
        INSERT INTO dex_candidate_pool_pairs (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            anchor_address, counter_address, address_pair, pool_count,
            fixed_fee_liquid_pool_count, priced_pool_count,
            yc_optimal_pool_id, zc_optimal_pool_id, price_reference_json,
            price_outlier_filter, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        common_pair,
    )
    connection.execute(
        """
        INSERT INTO dex_candidate_abnormal_pool_pairs (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            anchor_address, counter_address, address_pair, pool_count,
            fixed_fee_liquid_pool_count, priced_pool_count,
            yc_optimal_pool_id, zc_optimal_pool_id, price_reference_json,
            price_outlier_filter, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        common_pair,
    )
    for table, pool_id, token0, token1, first, third in (
        ("dex_candidate_pools", "pool-yc", ANCHOR, Y, 1, 0),
        ("dex_candidate_pools", "pool-zc", Y, ANCHOR, 0, 1),
        ("dex_candidate_abnormal_pools", "pool-yc", ANCHOR, Y, 1, 0),
        ("dex_candidate_abnormal_pools", "pool-zc", Y, ANCHOR, 0, 1),
    ):
        connection.execute(
            f"""
            INSERT INTO {table} (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                address_pair, pool_id, token0, token1, fee, fee_label,
                dynamic_fee, special_fee, tick_spacing, hooks, sqrt_price_x96,
                tick, has_liquidity, liquidity, liquidity_index,
                estimated_tvl_usd, balances_json, depth_status, depth_band_json,
                usable_for_observation, is_first_hop_recommended,
                is_third_hop_recommended, hop_recommendation_json,
                optimal_roles_json, liquidity_read_at, liquidity_block_number,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                NETWORK,
                CHAIN_ID,
                PROTOCOL,
                ANCHOR_SCOPE,
                ANCHOR_SYMBOL,
                f"{token0}:{token1}",
                pool_id,
                token0,
                token1,
                3000,
                3000,
                0,
                0,
                60,
                "",
                "79228162514264337593543950336",
                0,
                1,
                "1000000000000000000000",
                1000.0,
                1_000_000.0,
                "{}",
                "liquid",
                "{}",
                1,
                first,
                third,
                json.dumps({"first": bool(first), "third": bool(third)}),
                json.dumps({"yc": bool(first), "zc": bool(third)}),
                STAMP,
                1,
                STAMP,
                STAMP,
            ),
        )
    for table in ("dex_candidate_pool_recommendations",):
        for hop, pool_id in (("first", "pool-yc"), ("third", "pool-zc")):
            connection.execute(
                f"""
                INSERT INTO {table} (
                    network, chain_id, protocol, anchor_scope, anchor_symbol,
                    counter_address, hop, rank, pool_id, depth_score, price,
                    fee, selection_basis, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    NETWORK,
                    CHAIN_ID,
                    PROTOCOL,
                    ANCHOR_SCOPE,
                    ANCHOR_SYMBOL,
                    Y,
                    hop,
                    pool_id,
                    1000.0,
                    1.0,
                    3000,
                    "legacy_fixed_fee",
                    STAMP,
                    STAMP,
                ),
            )
    for table, has_column, count_column, pool_ids in (
        ("market_observation_yz_pairs", "has_direct_swap_pool", "direct_swap_pool_count", ['"mid-yz"', '"mid-yz-extra"']),
        ("market_observation_abnormal_yz_pairs", "has_non_normal_swap_pool", "non_normal_swap_pool_count", ['"mid-yz"', '"mid-yz-extra"']),
    ):
        connection.execute(
            f"""
            INSERT INTO {table} (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                direction_key, token_x_address, token_y_address, address_pair,
                {has_column}, {count_column}, pool_ids_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 2, ?, ?, ?)
            """,
            (
                NETWORK,
                CHAIN_ID,
                PROTOCOL,
                ANCHOR_SCOPE,
                ANCHOR_SYMBOL,
                f"{Y}:{Z}",
                Y,
                Z,
                f"{Z}:{Y}",
                "[" + ",".join(pool_ids) + "]",
                STAMP,
                STAMP,
            ),
        )
    connection.execute(
        f"""
        INSERT INTO market_observation_yz_pairs (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            direction_key, token_x_address, token_y_address, address_pair,
            has_direct_swap_pool, direct_swap_pool_count, pool_ids_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)
        """,
        (
            NETWORK,
            CHAIN_ID,
            PROTOCOL,
            ANCHOR_SCOPE,
            ANCHOR_SYMBOL,
            f"{Z}:{Y}",
            Z,
            Y,
            f"{Z}:{Y}",
            '["mid-zy"]',
            STAMP,
            STAMP,
        ),
    )
    connection.execute(
        f"""
        INSERT INTO market_observation_abnormal_yz_pairs (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            direction_key, token_x_address, token_y_address, address_pair,
            has_non_normal_swap_pool, non_normal_swap_pool_count, pool_ids_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?)
        """,
        (
            NETWORK,
            CHAIN_ID,
            PROTOCOL,
            ANCHOR_SCOPE,
            ANCHOR_SYMBOL,
            f"{Z}:{Y}",
            Z,
            Y,
            f"{Z}:{Y}",
            '["mid-zy"]',
            STAMP,
            STAMP,
        ),
    )


def _build_legacy_db(path: str) -> None:
    with _connect(path) as connection:
        _create_legacy_schema(connection)
        _seed_legacy_rows(connection)
        connection.commit()


def _rows(path: str, sql: str) -> list[sqlite3.Row]:
    with _connect(path) as connection:
        return connection.execute(sql).fetchall()


def _assert_directional_candidate_rows(path: str, prefix: str = "dex_candidate_pool") -> None:
    pair_table = f"{prefix}_pairs"
    pairs = _rows(
        path,
        f"""
        SELECT direction_key, anchor_address, counter_address,
               yc_optimal_pool_id, zc_optimal_pool_id
        FROM {pair_table}
        WHERE network = '{NETWORK}' AND chain_id = {CHAIN_ID}
        ORDER BY direction_key
        """,
    )
    assert len(pairs) == 2
    assert {str(row["direction_key"]) for row in pairs} == {f"{ANCHOR}:{Y}", f"{Y}:{ANCHOR}"}
    forward = next(row for row in pairs if str(row["direction_key"]) == f"{ANCHOR}:{Y}")
    reverse = next(row for row in pairs if str(row["direction_key"]) == f"{Y}:{ANCHOR}")
    assert str(forward["anchor_address"]) == ANCHOR
    assert str(forward["yc_optimal_pool_id"]) == "pool-yc"
    assert str(forward["zc_optimal_pool_id"] or "") == ""
    assert str(reverse["anchor_address"]) == Y
    assert str(reverse["yc_optimal_pool_id"] or "") == ""
    assert str(reverse["zc_optimal_pool_id"]) == "pool-zc"

    pool_table = f"{prefix}s"
    pools = _rows(
        path,
        f"""
        SELECT pool_id, address_pair, direction_key, direction_address_pair,
               is_first_hop_recommended, is_third_hop_recommended
        FROM {pool_table}
        WHERE network = '{NETWORK}' AND chain_id = {CHAIN_ID}
        ORDER BY pool_id
        """,
    )
    assert {str(row["pool_id"]) for row in pools} == {"pool-yc", "pool-zc"}
    yc_pool = next(row for row in pools if str(row["pool_id"]) == "pool-yc")
    zc_pool = next(row for row in pools if str(row["pool_id"]) == "pool-zc")
    assert str(yc_pool["address_pair"]) == f"{ANCHOR}:{Y}"
    assert str(yc_pool["direction_key"]) == f"{ANCHOR}:{Y}"
    assert str(yc_pool["direction_address_pair"]) == f"{ANCHOR}:{Y}"
    assert int(yc_pool["is_first_hop_recommended"]) == 1
    assert int(yc_pool["is_third_hop_recommended"]) == 0
    assert str(zc_pool["address_pair"]) == f"{Y}:{ANCHOR}"
    assert str(zc_pool["direction_key"]) == f"{Y}:{ANCHOR}"
    assert str(zc_pool["direction_address_pair"]) == f"{Y}:{ANCHOR}"
    assert int(zc_pool["is_first_hop_recommended"]) == 0
    assert int(zc_pool["is_third_hop_recommended"]) == 1

    if prefix == "dex_candidate_pool":
        recommendation_table = f"{prefix}_recommendations"
        recommendations = _rows(
            path,
            f"""
            SELECT direction_key, hop, pool_id
            FROM {recommendation_table}
            WHERE network = '{NETWORK}' AND chain_id = {CHAIN_ID}
            ORDER BY direction_key, hop
            """,
        )
        assert len(recommendations) == 2
        assert {
            (str(row["direction_key"]), str(row["hop"]), str(row["pool_id"]))
            for row in recommendations
        } == {
            (f"{ANCHOR}:{Y}", "first", "pool-yc"),
            (f"{Y}:{ANCHOR}", "third", "pool-zc"),
        }


def _assert_directional_observation_rows(path: str, table: str) -> None:
    rows = _rows(
        path,
        f"""
        SELECT direction_key, address_pair, pool_ids_json, optimal_pool_id
        FROM {table}
        WHERE network = '{NETWORK}' AND chain_id = {CHAIN_ID}
        ORDER BY direction_key
        """,
    )
    assert {str(row["direction_key"]) for row in rows} == {f"{Y}:{Z}", f"{Z}:{Y}"}
    forward = next(row for row in rows if str(row["direction_key"]) == f"{Y}:{Z}")
    reverse = next(row for row in rows if str(row["direction_key"]) == f"{Z}:{Y}")
    assert str(forward["address_pair"]) == f"{Y}:{Z}"
    assert str(forward["optimal_pool_id"]) == "mid-yz"
    assert json.loads(forward["pool_ids_json"]) == ["mid-yz"]
    assert str(reverse["address_pair"]) == f"{Z}:{Y}"
    assert str(reverse["optimal_pool_id"]) == "mid-zy"
    assert json.loads(reverse["pool_ids_json"]) == ["mid-zy"]


def test_legacy_business_schema_migrates_to_directional_rows(tmp_path) -> None:
    path = str(tmp_path / "legacy.sqlite3")
    _build_legacy_db(path)

    ensure_business_schema(path)

    _assert_directional_candidate_rows(path)
    _assert_directional_candidate_rows(path, prefix="dex_candidate_abnormal_pool")
    _assert_directional_observation_rows(path, "market_observation_yz_pairs")
    _assert_directional_observation_rows(path, "market_observation_abnormal_yz_pairs")
    assert len(_rows(path, "SELECT DISTINCT pool_id FROM dex_candidate_pools")) == 2
    assert len(_rows(path, "SELECT DISTINCT pool_id FROM dex_candidate_abnormal_pools")) == 2


def test_directional_migration_is_idempotent(tmp_path) -> None:
    path = str(tmp_path / "idempotent.sqlite3")
    _build_legacy_db(path)

    ensure_business_schema(path)
    ensure_business_schema(path)

    assert len(_rows(path, "SELECT * FROM dex_candidate_pool_pairs")) == 2
    assert len(_rows(path, "SELECT * FROM dex_candidate_pools")) == 2
    assert len(_rows(path, "SELECT * FROM dex_candidate_pool_recommendations")) == 2
    assert len(_rows(path, "SELECT * FROM market_observation_yz_pairs")) == 2
