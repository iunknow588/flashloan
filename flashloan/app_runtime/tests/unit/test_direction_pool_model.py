from core.db.builders import _build_loop_candidates, _candidate_rows
from core.db import builders
from core.db.direction_pool_score import (
    has_confirmed_liquidity,
    select_directional_pool_ids,
)
from core.db.storage_schema import ensure_database_schema
from core.db.storage_v4_exchange import (
    upsert_v4_exchange_pools,
    upsert_v4_liquidity_snapshots,
)


def test_minimum_liquidity_confirmation_uses_usd_then_one_token_fallback():
    assert has_confirmed_liquidity(
        {"estimated_tvl_usd": 1000, "liquidity_index": 0},
        "0x1",
    ) is True
    assert has_confirmed_liquidity(
        {"estimated_tvl_usd": 999, "liquidity_index": 0},
        "0x1",
    ) is False
    assert has_confirmed_liquidity(
        {"estimated_tvl_usd": 0, "liquidity_index": 1.0},
        "0x1",
    ) is True
    assert has_confirmed_liquidity(
        {"estimated_tvl_usd": 0, "liquidity_index": 0.9, "liquidity": ""},
        "0x1",
    ) is False


def _candidate_pool(
    *,
    pool_id: str,
    sqrt_price_x96: int,
    token0: str,
    token1: str,
) -> dict:
    return {
        "pool_id": pool_id,
        "token0": token0,
        "token1": token1,
        "fee": 3000,
        "fee_label": 3000,
        "dynamic_fee": False,
        "special_fee": False,
        "tick_spacing": 60,
        "hooks": "",
        "sqrt_price_x96": str(sqrt_price_x96),
        "has_liquidity": True,
        "liquidity": "1000000000000000000000",
        "liquidity_index": 1000,
        "estimated_tvl_usd": 1_000_000,
        "balances_json": "{}",
        "depth_status": "liquid",
        "depth_band_json": "{}",
        "liquidity_read_at": "2026-10-12T00:00:00Z",
        "liquidity_block_number": 1,
    }


def test_candidate_rows_keep_one_best_pool_per_direction():
    q96 = 1 << 96
    anchor = "0xaaa"
    counter = "0xbbb"
    high = _candidate_pool(
        pool_id="pool-high",
        sqrt_price_x96=q96 * 2,
        token0=anchor,
        token1=counter,
    )
    low = _candidate_pool(
        pool_id="pool-low",
        sqrt_price_x96=q96 // 2,
        token0=anchor,
        token1=counter,
    )
    no_liquid = _candidate_pool(
        pool_id="pool-no-liquid",
        sqrt_price_x96=q96 * 10,
        token0=anchor,
        token1=counter,
    )
    lowest = _candidate_pool(
        pool_id="pool-lowest",
        sqrt_price_x96=q96 // 5,
        token0=anchor,
        token1=counter,
    )
    no_liquid["estimated_tvl_usd"] = 0
    no_liquid["liquidity_index"] = 0
    no_liquid["liquidity"] = ""
    no_liquid["depth_status"] = "empty"
    rows = _candidate_rows(
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        anchor_scope="dex-arbitrage-46630-uniswap-v4",
        anchor_symbol="TZ",
        anchor_address=anchor,
        token_addresses=[counter],
        pools_by_edge={f"{anchor}:{counter}": [high, low, no_liquid, lowest]},
        tokens_meta={},
    )

    forward = next(pair for pair in rows["pairs"] if pair["direction_key"] == f"{anchor}:{counter}")
    reverse = next(pair for pair in rows["pairs"] if pair["direction_key"] == f"{counter}:{anchor}")
    assert forward["yc_optimal_pool_id"] == "pool-high"
    assert reverse["zc_optimal_pool_id"] == "pool-lowest"
    first_recs = [rec for rec in rows["recommendations"] if rec["hop"] == "first"]
    third_recs = [rec for rec in rows["recommendations"] if rec["hop"] == "third"]
    assert len(first_recs) == 3
    assert len(third_recs) == 3
    assert [rec["pool_id"] for rec in first_recs] == ["pool-high", "pool-low", "pool-lowest"]
    assert [rec["pool_id"] for rec in third_recs] == ["pool-lowest", "pool-low", "pool-high"]
    assert [rec["rank"] for rec in first_recs] == [1, 2, 3]
    assert [rec["rank"] for rec in third_recs] == [1, 2, 3]
    assert "pool-no-liquid" not in {rec["pool_id"] for rec in [*first_recs, *third_recs]}
    assert len(rows["pools"]) == 4
    assert {pool["pool_id"] for pool in rows["pools"]} == {
        "pool-high",
        "pool-low",
        "pool-no-liquid",
        "pool-lowest",
    }
    high_row = next(pool for pool in rows["pools"] if pool["pool_id"] == "pool-high")
    low_row = next(pool for pool in rows["pools"] if pool["pool_id"] == "pool-low")
    lowest_row = next(pool for pool in rows["pools"] if pool["pool_id"] == "pool-lowest")
    no_liquid_row = next(pool for pool in rows["pools"] if pool["pool_id"] == "pool-no-liquid")
    assert high_row["is_first_hop_recommended"] is True
    assert high_row["is_third_hop_recommended"] is True
    assert low_row["is_first_hop_recommended"] is True
    assert low_row["is_third_hop_recommended"] is True
    assert lowest_row["is_first_hop_recommended"] is True
    assert lowest_row["is_third_hop_recommended"] is True
    assert no_liquid_row["is_first_hop_recommended"] is False
    assert no_liquid_row["is_third_hop_recommended"] is False
    assert forward["yc_backup_pool_ids"] == ["pool-low", "pool-lowest"]
    assert reverse["zc_backup_pool_ids"] == ["pool-low", "pool-high"]
    assert "pool-no-liquid" not in forward["yc_backup_pool_ids"]
    assert "pool-no-liquid" not in reverse["zc_backup_pool_ids"]


def test_closed_loops_keep_two_directed_paths_separately():
    anchor = "0x" + "a" * 40
    y = "0x" + "b" * 40
    z = "0x" + "c" * 40
    candidate_pools = {
        "recommendations": [
            {"direction_key": f"{anchor}:{y}", "hop": "first", "pool_id": "yc-y", "selection_basis": "dir:yc-y"},
            {"direction_key": f"{z}:{anchor}", "hop": "third", "pool_id": "zc-y", "selection_basis": "dir:zc-y"},
            {"direction_key": f"{anchor}:{z}", "hop": "first", "pool_id": "yc-z", "selection_basis": "dir:yc-z"},
            {"direction_key": f"{y}:{anchor}", "hop": "third", "pool_id": "zc-z", "selection_basis": "dir:zc-z"},
        ]
    }
    observation_pools = {
        "pools": [
            {"pool_id": "middle-y-z", "token0": y, "token1": z},
            {"pool_id": "middle-z-y", "token0": z, "token1": y},
        ]
    }
    observation_pairs = [
        {"direction_key": f"{y}:{z}", "optimal_pool_id": "middle-y-z", "pool_ids_json": '["middle-y-z"]'},
        {"direction_key": f"{z}:{y}", "optimal_pool_id": "middle-z-y", "pool_ids_json": '["middle-z-y"]'},
    ]
    loops, hops = _build_loop_candidates(
        anchor_address=anchor,
        anchor_symbol="TZ",
        token_addresses=[y, z],
        candidate_pools=candidate_pools,
        observation_pools=observation_pools,
        observation_pairs=observation_pairs,
        token_symbols={y: "TB", z: "TA"},
    )

    assert {loop["loop_key"] for loop in loops} == {f"{anchor}:{y}:{z}", f"{anchor}:{z}:{y}"}
    assert all(loop["path_addresses"] == [anchor, y, z, anchor] or loop["path_addresses"] == [anchor, z, y, anchor] for loop in loops)
    assert all(
        "backup_pool_ids" in loop and loop["backup_pool_ids"].get("first", []) == []
        and loop["backup_pool_ids"].get("third", []) == []
        for loop in loops
    )
    assert len(hops) == 6
    assert all(
        (hop["pool_selection_basis"] or "").startswith(("dir:", "observation_direction_optimal_pool:"))
        for hop in hops
    )


def test_stock_anchor_builds_candidate_observation_and_directed_loops(tmp_path):
    database = str(tmp_path / "stock-anchor.sqlite3")
    ensure_database_schema(database)
    zero = "0x0000000000000000000000000000000000000000"
    aapl = "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"
    usde = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
    usdg = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
    pools = [
        {
            "pool_id": "pool-aapl-eth",
            "pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "pool_key": {"currency0": aapl, "currency1": zero, "fee": 3000, "tickSpacing": 60, "hooks": zero},
            "token0": aapl,
            "token1": zero,
            "fee": 3000,
            "tick_spacing": 60,
            "hooks": zero,
        },
        {
            "pool_id": "pool-eth-usde",
            "pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "pool_key": {"currency0": zero, "currency1": usde, "fee": 3000, "tickSpacing": 60, "hooks": zero},
            "token0": zero,
            "token1": usde,
            "fee": 3000,
            "tick_spacing": 60,
            "hooks": zero,
        },
        {
            "pool_id": "pool-usde-aapl",
            "pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "pool_key": {"currency0": usde, "currency1": aapl, "fee": 3000, "tickSpacing": 60, "hooks": zero},
            "token0": usde,
            "token1": aapl,
            "fee": 3000,
            "tick_spacing": 60,
            "hooks": zero,
        },
        {
            "pool_id": "pool-aapl-usdg",
            "pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "pool_key": {"currency0": aapl, "currency1": usdg, "fee": 3000, "tickSpacing": 60, "hooks": zero},
            "token0": aapl,
            "token1": usdg,
            "fee": 3000,
            "tick_spacing": 60,
            "hooks": zero,
        },
    ]
    upsert_v4_exchange_pools(
        database,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        pool_rows=pools,
    )
    upsert_v4_liquidity_snapshots(
        database,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        rows=[
            {
                "pool_id": "pool-aapl-eth",
                "has_liquidity": True,
                "liquidity_index": 1000,
                "estimated_tvl_usd": 1_000_000,
                "sqrt_price_x96": str(2**96),
                "recent_swap_active": True,
            },
            {
                "pool_id": "pool-eth-usde",
                "has_liquidity": True,
                "liquidity_index": 1000,
                "estimated_tvl_usd": 1_000_000,
                "sqrt_price_x96": str(2**96),
                "recent_swap_active": True,
            },
            {
                "pool_id": "pool-usde-aapl",
                "has_liquidity": True,
                "liquidity_index": 1000,
                "estimated_tvl_usd": 1_000_000,
                "sqrt_price_x96": str(2**96),
                "recent_swap_active": True,
            },
            {
                "pool_id": "pool-aapl-usdg",
                "has_liquidity": True,
                "liquidity_index": 1000,
                "estimated_tvl_usd": 1_000_000,
                "sqrt_price_x96": str(2**96),
                "recent_swap_active": True,
            },
        ],
    )

    report = builders.build_all_anchor_data(
        database,
        network="robinhood",
        chain_id=4663,
        anchors=("AAPL",),
    )
    assert report["ok"] is True
    aapl_report = next(item for item in report["anchors"] if item["anchorSymbol"] == "AAPL")
    assert aapl_report["candidate"]["tokenCount"] == 3
    assert aapl_report["observation"]["tokenCount"] == 3
    assert aapl_report["observation"]["directionCount"] == 6
    assert aapl_report["loop"]["loopCount"] == 2

    from core.db.storage_business_io import query

    token = query(
        database,
        "SELECT anchor_symbol FROM chain_tokens WHERE network = ? AND chain_id = ? AND token_address = ?",
        ("robinhood", 4663, aapl),
    )
    assert token and token[0]["anchor_symbol"] == "AAPL"
    pairs = query(
        database,
        """
        SELECT direction_key, yc_optimal_pool_id, zc_optimal_pool_id
        FROM dex_candidate_pool_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY direction_key
        """,
        ("robinhood", 4663, "uniswap_v4", "AAPL"),
    )
    assert {row["direction_key"] for row in pairs} == {
        f"{aapl}:{zero}",
        f"{aapl}:{usdg}",
        f"{aapl}:{usde}",
        f"{zero}:{aapl}",
        f"{usdg}:{aapl}",
        f"{usde}:{aapl}",
    }
    assert all(
        bool(row["yc_optimal_pool_id"]) or bool(row["zc_optimal_pool_id"])
        for row in pairs
    )


def test_select_directional_pool_ids_skips_dead_pools_and_keeps_primary_order():
    q96 = 1 << 96
    high = _candidate_pool(
        pool_id="pool-high",
        sqrt_price_x96=q96 * 2,
        token0="0xaaa",
        token1="0xbbb",
    )
    low = _candidate_pool(
        pool_id="pool-low",
        sqrt_price_x96=q96 // 2,
        token0="0xaaa",
        token1="0xbbb",
    )
    dead = _candidate_pool(
        pool_id="pool-dead",
        sqrt_price_x96=q96 * 10,
        token0="0xaaa",
        token1="0xbbb",
    )
    dead["estimated_tvl_usd"] = 0
    dead["liquidity_index"] = 0
    dead["liquidity"] = ""
    dead["depth_status"] = "empty"

    selected = select_directional_pool_ids(
        [dead, low, high],
        "0xaaa",
        "0xbbb",
        limit=3,
    )

    assert selected == ["pool-high", "pool-low"]


def test_testnet_db_observation_rows_use_primary_and_backup_without_no_depth_item(monkeypatch):
    from dex.intent_trade.chains.robinhood_testnet import market_observation as mo

    anchor = "0x" + "a" * 40
    y = "0x" + "b" * 40
    z = "0x" + "c" * 40
    q96 = 1 << 96

    def obs_pool(pool_id, *, has_liquidity, sqrt_price):
        return {
            "pool": pool_id,
            "poolId": pool_id,
            "token0": y,
            "token1": z,
            "token0Symbol": "TB",
            "token1Symbol": "TA",
            "symbolPair": "TB:TA",
            "addressPair": f"{y}:{z}",
            "fee": 3000,
            "dynamicFee": False,
            "specialFee": False,
            "hooks": "0x0000000000000000000000000000000000000000",
            "hasLiquidity": has_liquidity,
            "liquidityIndex": 1000.0 if has_liquidity else 0.0,
            "estimatedTvlUsd": 1_000_000 if has_liquidity else 0,
            "sqrtPriceX96": str(sqrt_price),
            "liquidity": "1000000000000000000000" if has_liquidity else "",
            "balances": {},
            "depthStatus": "liquid" if has_liquidity else "no_liquidity",
        }

    primary = obs_pool("pool-primary", has_liquidity=False, sqrt_price=q96 * 2)
    backup_one = obs_pool("pool-backup-1", has_liquidity=True, sqrt_price=q96 // 2)
    backup_two = obs_pool("pool-backup-2", has_liquidity=True, sqrt_price=q96 // 5)
    no_depth_non_normal = {
        **backup_one,
        "pool": "pool-non-normal-no-depth",
        "poolId": "pool-non-normal-no-depth",
        "fee": 5000,
        "hasLiquidity": False,
        "liquidityIndex": 0.0,
        "estimatedTvlUsd": 0,
    }
    monkeypatch.setattr(
        mo,
        "_testnet_db_observation_directions",
        lambda anchor_groups: [
            {
                "anchorSymbol": "TZ",
                "anchorAddress": anchor,
                "addressPair": f"{y}:{z}",
                "tokenX": y,
                "tokenY": z,
                "tokenXSymbol": "TB",
                "tokenYSymbol": "TA",
                "token0": y,
                "token1": z,
                "poolIds": ["pool-primary", "pool-backup-1", "pool-backup-2"],
                "poolCount": 3,
                "hasDirectSwapPool": True,
            }
        ],
    )
    rows, pool_rows, _ = mo._testnet_db_observation_rows(
        [
            {
                "anchorSymbol": "TZ",
                "anchorAddresses": [anchor],
                "sharedTokens": [
                    {"symbol": "TB", "address": y},
                    {"symbol": "TA", "address": z},
                ],
            }
        ],
        normal_pools=[primary, backup_one, backup_two],
        non_normal_pools=[no_depth_non_normal],
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["hasDirectSwapPool"] is True
    assert [pool["poolId"] for pool in row["directSwapPools"]] == [
        "pool-backup-1",
        "pool-backup-2",
    ]
    assert row["directSwapPools"][0]["recommendedPoolRole"] == "primary"
    assert row["directSwapPools"][1]["recommendedPoolRole"] == "backup"
    assert row["primaryPoolIds"] == ["pool-backup-1"]
    assert row["backupPoolIds"] == ["pool-backup-2"]
    assert row["nonNormalSwapPools"] == []
    assert row["hasNonNormalSwapPool"] is False
    assert "pool-primary" not in {pool["poolId"] for pool in pool_rows}
