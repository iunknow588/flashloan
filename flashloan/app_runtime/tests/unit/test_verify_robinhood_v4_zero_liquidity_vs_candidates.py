from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = (
    REPO_ROOT
    / "scripts"
    / "verify_robinhood_v4_zero_liquidity_vs_candidates.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "zero_liquidity_vs_candidates_verifier",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ensure_schema(tmp_path: Path) -> str:
    mod = _load_module()
    database = str((tmp_path / "zero.sqlite3").resolve())
    mod._ensure_sys_path()
    from core.db.storage_business_schema import ensure_business_schema

    ensure_business_schema(database)
    return database


def _insert_pool(
    database: str,
    *,
    pool_id: str,
    token0: str,
    token1: str,
) -> None:
    mod = _load_module()
    mod._ensure_sys_path()
    from core.db.storage_business_io import executemany

    now = "2026-10-24T00:00:00Z"
    executemany(
        database,
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_identity_mode,
            pool_manager, pool_key_json, token0, token1, fee, fee_label,
            dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                mod.NETWORK,
                mod.CHAIN_ID,
                mod.PROTOCOL,
                pool_id,
                "v4_pool_id",
                "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                '{"initializeBlockNumber": 200}',
                token0,
                token1,
                3000,
                3000,
                0,
                0,
                60,
                "0x" + "00" * 20,
                f"{token0}:{token1}",
                now,
                now,
            )
        ],
    )
    executemany(
        database,
        """
        INSERT INTO v4_exchange_pool_liquidity_snapshots (
            network, chain_id, protocol, pool_id, has_liquidity, liquidity,
            estimated_tvl_usd, depth_status, observed_at, refreshed_at,
            evidence_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                mod.NETWORK,
                mod.CHAIN_ID,
                mod.PROTOCOL,
                pool_id,
                0,
                "0",
                0.0,
                "empty",
                now,
                now,
                "pool_manager_extsload_live_state_batch",
            )
        ],
    )


def _insert_candidate(
    database: str,
    *,
    pool_id: str,
    token0: str,
    token1: str,
) -> None:
    mod = _load_module()
    mod._ensure_sys_path()
    from core.db.storage_business_io import executemany

    now = "2026-10-24T00:00:00Z"
    executemany(
        database,
        """
        INSERT INTO dex_candidate_pools (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            address_pair, direction_key, pool_id, token0, token1, fee,
            dynamic_fee, special_fee, tick_spacing, hooks, has_liquidity,
            usable_for_observation, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                mod.NETWORK,
                mod.CHAIN_ID,
                mod.PROTOCOL,
                "dex-arbitrage-46630-uniswap-v4",
                "ETH",
                f"{token0}:{token1}",
                f"{token0}:{token1}",
                pool_id,
                token0,
                token1,
                3000,
                0,
                0,
                60,
                "0x" + "00" * 20,
                0,
                1,
                now,
                now,
            )
        ],
    )
    executemany(
        database,
        """
        INSERT INTO dex_candidate_pool_recommendations (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            counter_address, direction_key, hop, rank, pool_id, created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                mod.NETWORK,
                mod.CHAIN_ID,
                mod.PROTOCOL,
                "dex-arbitrage-46630-uniswap-v4",
                "ETH",
                token1,
                f"{token0}:{token1}",
                "first",
                1,
                pool_id,
                now,
                now,
            )
        ],
    )


def _insert_closed_loop(
    database: str,
    *,
    pool_id: str,
    loop_key: str,
    pool_ids_json: str | None = None,
    insert_hop: bool = True,
) -> None:
    mod = _load_module()
    mod._ensure_sys_path()
    from core.db.storage_business_io import executemany

    now = "2026-10-24T00:00:00Z"
    anchor = "0x" + "00" * 20
    token = "0x" + "11" * 20
    executemany(
        database,
        """
        INSERT INTO v4_closed_loops (
            network, chain_id, protocol, market_id, loop_key, anchor_address,
            anchor_symbol, pool_ids_json, recommended_first_priority, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                mod.NETWORK,
                mod.CHAIN_ID,
                mod.PROTOCOL,
                "dex-arbitrage-46630-uniswap-v4",
                loop_key,
                anchor,
                "ETH",
                json.dumps([pool_id]) if pool_ids_json is None else pool_ids_json,
                0,
                "active",
                now,
                now,
            )
        ],
    )
    if insert_hop:
        executemany(
            database,
            """
            INSERT INTO v4_closed_loop_hops (
                network, chain_id, protocol, market_id, loop_key, hop_index,
                hop_name, input_address, output_address, pool_id, base_pool_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    mod.NETWORK,
                    mod.CHAIN_ID,
                    mod.PROTOCOL,
                    "dex-arbitrage-46630-uniswap-v4",
                    loop_key,
                    0,
                    "first",
                    anchor,
                    token,
                    pool_id,
                    pool_id,
                    now,
                    now,
                )
            ],
        )


def test_count_residual_references_counts_candidates(tmp_path: Path) -> None:
    mod = _load_module()
    database = _ensure_schema(tmp_path)
    pool_id = "0x" + "aa" * 32
    token0 = "0x" + "00" * 20
    token1 = "0x" + "22" * 20
    _insert_pool(database, pool_id=pool_id, token0=token0, token1=token1)
    _insert_candidate(database, pool_id=pool_id, token0=token0, token1=token1)

    residuals = mod.count_residual_references(database, [pool_id])

    assert residuals["dex_candidate_pools"] == 1
    assert residuals["dex_candidate_pool_recommendations"] == 1
    assert residuals["v4_closed_loops"] == 0


def test_count_residual_references_counts_closed_loops(tmp_path: Path) -> None:
    mod = _load_module()
    database = _ensure_schema(tmp_path)
    pool_id = "0x" + "bb" * 32
    token0 = "0x" + "00" * 20
    token1 = "0x" + "33" * 20
    _insert_pool(database, pool_id=pool_id, token0=token0, token1=token1)
    _insert_closed_loop(
        database,
        pool_id=pool_id,
        loop_key=f"loop-{pool_id[-16:]}",
    )

    residuals = mod.count_residual_references(database, [pool_id])

    assert residuals["v4_closed_loop_hops"] == 1
    assert residuals["v4_closed_loops"] == 1
    assert residuals["v4_closed_loops_by_hop_refs"] == 1
    assert residuals["v4_closed_loops_by_pool_ids_json"] == 1


def test_count_residual_references_unions_distinct_loop_ref_sources(tmp_path: Path) -> None:
    mod = _load_module()
    database = _ensure_schema(tmp_path)
    pool_id = "0x" + "dd" * 32
    token0 = "0x" + "00" * 20
    token1 = "0x" + "55" * 20
    _insert_pool(database, pool_id=pool_id, token0=token0, token1=token1)
    _insert_closed_loop(
        database,
        pool_id=pool_id,
        loop_key="loop-hop-only",
        pool_ids_json=json.dumps(["0x" + "ee" * 32]),
        insert_hop=True,
    )
    _insert_closed_loop(
        database,
        pool_id=pool_id,
        loop_key="loop-json-only",
        pool_ids_json=json.dumps([pool_id]),
        insert_hop=False,
    )

    residuals = mod.count_residual_references(database, [pool_id])

    assert residuals["v4_closed_loops_by_hop_refs"] == 1
    assert residuals["v4_closed_loops_by_pool_ids_json"] == 1
    assert residuals["v4_closed_loops"] == 2


def test_load_cleanup_evidence_filters_reports(tmp_path: Path) -> None:
    mod = _load_module()
    idle_root = tmp_path / "idle_maintenance"
    cleanup_dir = idle_root / "20261024T000000Z"
    cleanup_dir.mkdir(parents=True)
    (cleanup_dir / "report.json").write_text(
        json.dumps(
            {
                "mode": "zero_liquidity_pool_cleanup",
                "ok": True,
                "generatedAt": "2026-10-24T00:00:00Z",
                "phase": "completed",
                "cleanedPoolCount": 2,
                "poolIds": ["0x" + "aa" * 32, "0x" + "bb" * 32],
                "cleaned": {"dex_candidate_pools": 2},
            }
        ),
        encoding="utf-8",
    )
    idle_dir = idle_root / "20261024T000100Z"
    idle_dir.mkdir(parents=True)
    (idle_dir / "report.json").write_text(
        json.dumps({"mode": "idle_maintenance", "ok": True}),
        encoding="utf-8",
    )

    evidence = mod.load_cleanup_evidence(idle_root)

    assert evidence["cleanupEvidenceCount"] == 1
    assert evidence["latest"]["cleanedPoolCount"] == 2
    assert evidence["latest"]["poolIdCount"] == 2
    assert evidence["recent"][0]["generatedAt"] == "2026-10-24T00:00:00Z"


def test_build_report_flags_residuals_and_read_only_safety(tmp_path: Path) -> None:
    mod = _load_module()
    database = _ensure_schema(tmp_path)
    pool_id = "0x" + "cc" * 32
    token0 = "0x" + "00" * 20
    token1 = "0x" + "44" * 20
    _insert_pool(database, pool_id=pool_id, token0=token0, token1=token1)
    _insert_candidate(database, pool_id=pool_id, token0=token0, token1=token1)

    report = mod.build_report(
        database,
        idle_evidence_root=tmp_path / "idle_maintenance",
        generated_at="2026-10-24T01:00:00Z",
    )

    assert report["explicitZeroLiquidityPoolCount"] == 1
    assert report["residualReferenceTotal"] == 2
    assert "residual candidate/loop references" in report["conclusion"]
    assert report["safety"]["writesSqlite"] is False
    assert report["safety"]["mainnetBroadcastEnabled"] is False


def test_build_report_clean_when_no_zero_pools(tmp_path: Path) -> None:
    mod = _load_module()
    database = _ensure_schema(tmp_path)

    report = mod.build_report(
        database,
        idle_evidence_root=tmp_path / "idle_maintenance",
        generated_at="2026-10-24T01:00:00Z",
    )

    assert report["explicitZeroLiquidityPoolCount"] == 0
    assert report["residualReferenceTotal"] == 0
    assert "No explicit zero-liquidity pools" in report["conclusion"]
