from core.db.builders import _build_loop_candidates, _candidate_rows
from core.db import builders
from core.db.direction_pool_score import (
    has_confirmed_liquidity,
    select_directional_pool_ids,
)
from core.db.storage_business_io import query
from core.db.storage_market_observation import replace_market_observation_tables
from core.db.storage_schema import ensure_database_schema
from core.db.storage_v4_exchange import (
    upsert_v4_exchange_pools,
    upsert_v4_liquidity_snapshots,
)
from core.db.token_symbols import is_unresolved_placeholder_symbol
from dex.intent_trade.chains.robinhood.dex_pool_candidates import (
    _decorate_candidates_with_live_state,
)


def test_minimum_liquidity_confirmation_uses_usd_then_one_token_fallback():
    assert has_confirmed_liquidity(
        {"estimated_tvl_usd": 1000, "liquidity_index": 0},
    ) is True
    assert has_confirmed_liquidity(
        {"estimated_tvl_usd": 999, "liquidity_index": 0},
    ) is False
    assert has_confirmed_liquidity(
        {"estimated_tvl_usd": 0, "liquidity_index": 1.0},
    ) is True
    assert has_confirmed_liquidity(
        {"estimated_tvl_usd": 0, "liquidity_index": 0.9, "liquidity": ""},
    ) is False


def test_live_onchain_extsload_state_confirms_thin_indexed_pool():
    """PoolManager extsload live state is a generic confirmation of real
    liquidity even when the derived liquidity index is below one token."""
    assert has_confirmed_liquidity(
        {
            "has_liquidity": True,
            "liquidity": "123456789012345678",
            "liquidity_index": 0.123,
            "estimated_tvl_usd": 0,
            "evidence_source": "pool_manager_extsload_live_state_batch",
        }
    ) is True
    assert has_confirmed_liquidity(
        {
            "has_liquidity": False,
            "liquidity": "123456789012345678",
            "liquidity_index": 0.123,
            "estimated_tvl_usd": 0,
            "evidence_source": "pool_manager_extsload_live_state_batch",
        }
    ) is False


def test_unresolved_placeholder_symbol_rule_is_generic():
    assert is_unresolved_placeholder_symbol("0X60042B65") is True
    assert is_unresolved_placeholder_symbol("0x102154e7") is True
    assert is_unresolved_placeholder_symbol("TA") is False
    assert is_unresolved_placeholder_symbol("TB") is False
    assert is_unresolved_placeholder_symbol("TZ") is False
    assert is_unresolved_placeholder_symbol("ETH") is False
    assert is_unresolved_placeholder_symbol("0X123") is False
    assert is_unresolved_placeholder_symbol("0X123456789") is False
    assert has_confirmed_liquidity(
        {
            "has_liquidity": True,
            "liquidity": "0",
            "liquidity_index": 0,
            "estimated_tvl_usd": 0,
            "evidence_source": "pool_manager_extsload_live_state_batch",
        }
    ) is False


def test_confirmed_liquidity_marker_wins_over_small_usd_depth():
    """A tiny USD depth must not short-circuit the liquidity-index fallback.
    A confirmed pool with a large published liquidity index stays executable
    even when its derived USD depth is below the USD-only gate."""
    assert has_confirmed_liquidity(
        {
            "has_liquidity": True,
            "liquidity": "8820022023676511662838236763075056",
            "liquidity_index": 8.8e33,
            "estimated_tvl_usd": 0.08,
            "evidence_source": "",
        }
    ) is True
    assert has_confirmed_liquidity(
        {
            "has_liquidity": True,
            "liquidity": "",
            "liquidity_index": 0,
            "estimated_tvl_usd": 0,
            "evidence_source": "token_balance_scan",
        }
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
    assert len(rows["pools"]) == 3
    assert {pool["pool_id"] for pool in rows["pools"]} == {
        "pool-high",
        "pool-low",
        "pool-lowest",
    }
    high_row = next(pool for pool in rows["pools"] if pool["pool_id"] == "pool-high")
    low_row = next(pool for pool in rows["pools"] if pool["pool_id"] == "pool-low")
    lowest_row = next(pool for pool in rows["pools"] if pool["pool_id"] == "pool-lowest")
    assert high_row["is_first_hop_recommended"] is True
    assert high_row["is_third_hop_recommended"] is True
    assert low_row["is_first_hop_recommended"] is True
    assert low_row["is_third_hop_recommended"] is True
    assert lowest_row["is_first_hop_recommended"] is True
    assert lowest_row["is_third_hop_recommended"] is True
    assert forward["yc_backup_pool_ids"] == ["pool-low", "pool-lowest"]
    assert reverse["zc_backup_pool_ids"] == ["pool-low", "pool-high"]
    assert "pool-no-liquid" not in forward["yc_backup_pool_ids"]
    assert "pool-no-liquid" not in reverse["zc_backup_pool_ids"]


def test_candidate_rows_keep_five_directed_recommendations_and_reverse_order():
    q96 = 1 << 96
    anchor = "0xaaa"
    counter = "0xbbb"
    pools = [
        _candidate_pool(
            pool_id=f"p{index}",
            sqrt_price_x96=q96 * (100 + index),
            token0=counter,
            token1=anchor,
        )
        for index in range(1, 7)
    ]
    for pool in pools:
        pool["liquidity"] = ""
    rows = _candidate_rows(
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        anchor_scope="dex-arbitrage-46630-uniswap-v4",
        anchor_symbol="TZ",
        anchor_address=anchor,
        token_addresses=[counter],
        pools_by_edge={f"{anchor}:{counter}": pools},
        tokens_meta={},
    )

    forward = next(pair for pair in rows["pairs"] if pair["direction_key"] == f"{anchor}:{counter}")
    reverse = next(pair for pair in rows["pairs"] if pair["direction_key"] == f"{counter}:{anchor}")
    first_recs = [rec for rec in rows["recommendations"] if rec["hop"] == "first"]
    third_recs = [rec for rec in rows["recommendations"] if rec["hop"] == "third"]
    assert len(first_recs) == 5
    assert len(third_recs) == 5
    assert [rec["pool_id"] for rec in first_recs] == ["p1", "p2", "p3", "p4", "p5"]
    assert [rec["pool_id"] for rec in third_recs] == ["p6", "p5", "p4", "p3", "p2"]
    assert forward["yc_optimal_pool_id"] == "p1"
    assert reverse["zc_optimal_pool_id"] == "p6"
    assert len(forward["yc_backup_pool_ids"]) == 4
    assert len(reverse["zc_backup_pool_ids"]) == 4


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


def test_closed_loop_builder_skips_unresolved_or_missing_middle_symbols():
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
    common = {
        "anchor_address": anchor,
        "anchor_symbol": "TZ",
        "token_addresses": [y, z],
        "candidate_pools": candidate_pools,
        "observation_pools": observation_pools,
        "observation_pairs": observation_pairs,
    }

    unresolved_loops, _ = _build_loop_candidates(
        **common,
        token_symbols={y: "TB", z: "0X60042B65"},
    )
    missing_loops, _ = _build_loop_candidates(
        **common,
        token_symbols={y: "TB"},
    )
    resolved_loops, _ = _build_loop_candidates(
        **common,
        token_symbols={y: "TB", z: "TA"},
    )
    assert unresolved_loops == []
    assert missing_loops == []
    assert len(resolved_loops) == 2
    assert {tuple(loop["path_symbols"]) for loop in resolved_loops} == {
        ("TZ", "TB", "TA", "TZ"),
        ("TZ", "TA", "TB", "TZ"),
    }


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


def test_directional_selection_keeps_pool_inclusion_symmetric():
    """A physical pool must never make one side of a pair look available while
    the mirror side looks empty.

    Direction-specific output capacity can be asymmetric at extreme prices,
    but the V4 market itself is two-sided.  Pool inclusion therefore uses the
    same depth gate for both directions; direction only changes scoring.
    """
    q96 = 1 << 96
    anchor = "0x" + "a" * 40
    counter = "0x" + "b" * 40
    # Raw liquidity is below one token and USD evidence is missing, so the pool
    # is below the confirmed-depth gate even though one direction has enough
    # raw input capacity at the current extreme sqrt price.
    asymmetric = {
        "pool_id": "pool-asymmetric",
        "token0": anchor,
        "token1": counter,
        "fee": 3000,
        "fee_label": 3000,
        "dynamic_fee": False,
        "special_fee": False,
        "tick_spacing": 60,
        "hooks": "",
        "sqrt_price_x96": str(q96 // 1000),
        "has_liquidity": True,
        "liquidity": "100000000000000000",
        "liquidity_index": 0.1,
        "estimated_tvl_usd": 0,
        "balances_json": "{}",
        "depth_status": "thin",
        "depth_band_json": "{}",
        "liquidity_read_at": "2026-10-12T00:00:00Z",
        "liquidity_block_number": 1,
    }
    forward = select_directional_pool_ids([asymmetric], anchor, counter)
    reverse = select_directional_pool_ids([asymmetric], counter, anchor)
    assert forward == []
    assert reverse == []

    # Same physical pool with confirmed depth is available in both directions;
    # only the score/order may differ.
    deep = dict(asymmetric)
    deep["liquidity_index"] = 1000.0
    deep["estimated_tvl_usd"] = 1_000_000
    forward = select_directional_pool_ids([deep], anchor, counter)
    reverse = select_directional_pool_ids([deep], counter, anchor)
    assert forward == ["pool-asymmetric"]
    assert reverse == ["pool-asymmetric"]


def test_candidate_rows_keep_mirror_optimal_symmetry():
    """Builder recommendations must produce an optimal for both directions
    whenever the pair has any usable pool, or neither."""
    q96 = 1 << 96
    anchor = "0xaaa"
    counter = "0xbbb"
    pool = {
        "pool_id": "pool-only",
        "token0": anchor,
        "token1": counter,
        "fee": 3000,
        "fee_label": 3000,
        "dynamic_fee": False,
        "special_fee": False,
        "tick_spacing": 60,
        "hooks": "",
        "sqrt_price_x96": str(q96),
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
    dead = dict(pool)
    dead["pool_id"] = "pool-dead"
    dead["estimated_tvl_usd"] = 0
    dead["liquidity_index"] = 0
    dead["liquidity"] = ""
    dead["depth_status"] = "empty"
    rows = _candidate_rows(
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        anchor_scope="dex-arbitrage-46630-uniswap-v4",
        anchor_symbol="TZ",
        anchor_address=anchor,
        token_addresses=[counter],
        pools_by_edge={f"{anchor}:{counter}": [pool, dead]},
        tokens_meta={},
    )
    forward = next(pair for pair in rows["pairs"] if pair["direction_key"] == f"{anchor}:{counter}")
    reverse = next(pair for pair in rows["pairs"] if pair["direction_key"] == f"{counter}:{anchor}")
    assert forward["yc_optimal_pool_id"] == "pool-only"
    assert reverse["zc_optimal_pool_id"] == "pool-only"
    assert bool(forward["yc_optimal_pool_id"]) == bool(reverse["zc_optimal_pool_id"])


def test_market_observation_storage_keeps_mirror_direction_symmetry(tmp_path):
    """Observation storage must never make one direction empty while its mirror
    has pools, even when directional one-token capacity differs at price."""
    db = str(tmp_path / "observation-symmetry.sqlite3")
    ensure_database_schema(db)
    token_a = "0x" + "a" * 40
    token_b = "0x" + "b" * 40
    q96 = 1 << 96
    pool = {
        "pool_id": "pool-only",
        "poolId": "pool-only",
        "token0": token_a,
        "token1": token_b,
        "fee": 3000,
        "fee_label": 3000,
        "dynamic_fee": False,
        "special_fee": False,
        "tick_spacing": 60,
        "hooks": "",
        "has_liquidity": True,
        "liquidity": "100000000000000000",
        "liquidity_index": 0.1,
        "estimated_tvl_usd": 0,
        "sqrt_price_x96": str(1),
        "sqrtPriceX96": str(1),
        "balances": {},
        "depth_status": "thin",
        "depth_band": {},
        "liquidity_read_at": "2026-10-12T00:00:00Z",
        "liquidity_block_number": 1,
    }

    def directions(pool_row):
        return [
            {
                "token_x_address": token_a,
                "token_y_address": token_b,
                "direction_key": f"{token_a}:{token_b}",
                "all_pools": [pool_row],
            },
            {
                "token_x_address": token_b,
                "token_y_address": token_a,
                "direction_key": f"{token_b}:{token_a}",
                "all_pools": [pool_row],
            },
        ]

    replace_market_observation_tables(
        db,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        anchor_scope="dex-arbitrage-46630-uniswap-v4",
        anchor_symbol="TZ",
        directions=directions(pool),
        pools=[pool],
    )
    rows = query(
        db,
        """
        SELECT direction_key, has_direct_swap_pool, direct_swap_pool_count,
               pool_ids_json, optimal_pool_id, observation_empty_reason
        FROM market_observation_yz_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY direction_key
        """,
        ("robinhood-testnet", 46630, "uniswap_v4", "TZ"),
    )
    assert len(rows) == 2
    assert all(row["has_direct_swap_pool"] == 0 for row in rows)
    assert all(row["direct_swap_pool_count"] == 0 for row in rows)
    assert all(row["pool_ids_json"] == "[]" for row in rows)
    assert all(not row["optimal_pool_id"] for row in rows)
    assert all(row["observation_empty_reason"] == "no_confirmed_liquidity" for row in rows)

    deep = dict(pool)
    deep["liquidity_index"] = 1000.0
    deep["estimated_tvl_usd"] = 1_000_000
    replace_market_observation_tables(
        db,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        anchor_scope="dex-arbitrage-46630-uniswap-v4",
        anchor_symbol="TZ",
        directions=directions(deep),
        pools=[deep],
    )
    rows = query(
        db,
        """
        SELECT direction_key, has_direct_swap_pool, direct_swap_pool_count,
               pool_ids_json, optimal_pool_id, observation_empty_reason
        FROM market_observation_yz_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ? AND anchor_symbol = ?
        ORDER BY direction_key
        """,
        ("robinhood-testnet", 46630, "uniswap_v4", "TZ"),
    )
    assert len(rows) == 2
    assert all(row["has_direct_swap_pool"] == 1 for row in rows)
    assert all(row["direct_swap_pool_count"] == 1 for row in rows)
    assert all('"pool-only"' in row["pool_ids_json"] for row in rows)
    assert all(row["optimal_pool_id"] == "pool-only" for row in rows)
    assert all(not row["observation_empty_reason"] for row in rows)


def test_mainnet_live_state_unavailable_stays_unknown_not_false(monkeypatch):
    """A failed/unknown live state read must not flip hasLiquidity to False."""
    pool_id = "0x" + "cc" * 32
    healthy_id = "0x" + "ab" * 32
    candidate = {
        "pool": pool_id,
        "poolId": pool_id,
        "fee": 3000,
        "poolKey": {"fee": 3000},
        "hooks": "",
        "hasLiquidity": True,
        "liquidity": "1000000000000000000000",
    }
    healthy = {
        "pool": healthy_id,
        "poolId": healthy_id,
        "fee": 3000,
        "poolKey": {"fee": 3000},
        "hooks": "",
        "hasLiquidity": True,
    }

    def fake_batch(**kwargs):
        return {
            pool_id: {"error": "RPC timeout", "source": "pool_manager_extsload_live_state_batch"},
            healthy_id: {
                "liquidity": "1000000000000000000000",
                "sqrtPriceX96": str(2**96),
                "tick": 0,
                "source": "pool_manager_extsload_live_state_batch",
            },
        }

    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood.dex_pool_candidates._batch_read_v4_pool_states",
        fake_batch,
    )
    summary = _decorate_candidates_with_live_state(
        [candidate, healthy],
        pool_manager="0x" + "66" * 20,
        rpc_url="https://fake-mainnet.example",
    )
    assert candidate["hasLiquidity"] is True
    assert candidate["liquidity"] == "1000000000000000000000"
    assert candidate["depthStatus"] == "live_liquidity_unavailable"
    assert summary["liveStateUnavailablePoolCount"] == 1
    assert summary["liveNoLiquidityPoolCount"] == 0


def test_mainnet_live_state_zero_is_explicit_false(monkeypatch):
    pool_id = "0x" + "dd" * 32
    healthy_id = "0x" + "cd" * 32
    candidate = {
        "pool": pool_id,
        "poolId": pool_id,
        "fee": 3000,
        "poolKey": {"fee": 3000},
        "hooks": "",
        "hasLiquidity": True,
    }
    healthy = {
        "pool": healthy_id,
        "poolId": healthy_id,
        "fee": 3000,
        "poolKey": {"fee": 3000},
        "hooks": "",
        "hasLiquidity": True,
    }

    def fake_batch(**kwargs):
        return {
            pool_id: {
                "error": "robinhood mainnet pool manager live liquidity is zero",
                "source": "pool_manager_extsload_live_state_batch",
            },
            healthy_id: {
                "liquidity": "1000000000000000000000",
                "sqrtPriceX96": str(2**96),
                "tick": 0,
                "source": "pool_manager_extsload_live_state_batch",
            },
        }

    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood.dex_pool_candidates._batch_read_v4_pool_states",
        fake_batch,
    )
    summary = _decorate_candidates_with_live_state(
        [candidate, healthy],
        pool_manager="0x" + "66" * 20,
        rpc_url="https://fake-mainnet.example",
    )
    assert candidate["hasLiquidity"] is False
    assert candidate["depthStatus"] == "live_liquidity_zero"
    assert candidate["liquiditySource"] == "pool_manager_extsload_live_state_batch"
    assert summary["liveNoLiquidityPoolCount"] == 1
    assert summary["liveStateUnavailablePoolCount"] == 0


def test_direction_symmetry_violations_detect_all_one_sided_surfaces():
    violations = builders._direction_symmetry_violations(
        candidate_pairs=[
            {
                "direction_key": "0xaaa:0xbbb",
                "yc_optimal_pool_id": "pool-fwd",
                "zc_optimal_pool_id": "",
                "yc_backup_pool_ids_json": '["pool-backup"]',
                "zc_backup_pool_ids_json": "[]",
            },
            {
                "direction_key": "0xbbb:0xaaa",
                "yc_optimal_pool_id": "",
                "zc_optimal_pool_id": "",
                "yc_backup_pool_ids_json": "[]",
                "zc_backup_pool_ids_json": "[]",
            },
        ],
        observation_directions=[
            {
                "direction_key": "0xccc:0xddd",
                "has_direct_swap_pool": 1,
                "direct_swap_pool_count": 1,
            },
            {
                "direction_key": "0xddd:0xccc",
                "has_direct_swap_pool": 0,
                "direct_swap_pool_count": 0,
            },
        ],
        loop_keys=["0xeee:0xfff:0x111"],
    )
    assert {item["table"] for item in violations} == {
        "dex_candidate_pool_pairs",
        "market_observation_yz_pairs",
        "v4_closed_loops",
    }


def test_direction_symmetry_violations_report_invalid_backup_json():
    violations = builders._direction_symmetry_violations(
        candidate_pairs=[
            {
                "direction_key": "0xaaa:0xbbb",
                "yc_optimal_pool_id": "pool-fwd",
                "zc_optimal_pool_id": "",
                "yc_backup_pool_ids_json": "{}",
                "zc_backup_pool_ids_json": "{}",
            },
            {
                "direction_key": "0xbbb:0xaaa",
                "yc_optimal_pool_id": "",
                "zc_optimal_pool_id": "pool-rev",
                "yc_backup_pool_ids_json": "{}",
                "zc_backup_pool_ids_json": "{}",
            },
        ],
        observation_directions=[],
        loop_keys=[],
    )
    assert any(
        item.get("invalid_backup_json") is True
        for item in violations
    )


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
