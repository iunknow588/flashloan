from __future__ import annotations

from core.db.direction_integrity import build_v4_integrity_report
from core.db.storage_business_schema import ensure_business_schema
from core.db.storage_chain_core import seed_chain_tokens
from core.db.storage_dex_candidate import replace_dex_candidate_tables
from core.db.storage_dex_surface_snapshot import save_dex_overview_snapshot
from core.db.storage_market_observation import replace_market_observation_tables
from core.db.storage_v4_closed_loop import replace_v4_closed_loop_tables
from core.db.storage_v4_exchange import (
    upsert_v4_exchange_pools,
    upsert_v4_liquidity_snapshots,
)


ETH = "0x0000000000000000000000000000000000000000"
TZ = "0x0000000000000000000000000000000000000001"
TB = "0x0000000000000000000000000000000000000002"
NETWORK = "robinhood-testnet"
CHAIN_ID = 46630
PROTOCOL = "uniswap_v4"
MARKET_ID = "dex-arbitrage-46630-uniswap-v4"
P1 = "0x" + "0" * 63 + "1"
P2 = "0x" + "0" * 63 + "2"
P3 = "0x" + "0" * 63 + "3"


def _pool(pool_id, token0, token1, *, has_liquidity=1):
    return {
        "pool_id": pool_id,
        "pool_identity_mode": "v4_pool_id",
        "pool_manager": ETH,
        "pool_key": {
            "currency0": token0,
            "currency1": token1,
            "fee": 3000,
            "hooks": ETH,
        },
        "token0": token0,
        "token1": token1,
        "fee": 3000,
        "hooks": ETH,
        "tick_spacing": 60,
        "has_liquidity": has_liquidity,
        "liquidity": "1000000000000000000000" if has_liquidity else "0",
        "liquidity_index": 1000.0 if has_liquidity else 0.0,
        "estimated_tvl_usd": 2000.0 if has_liquidity else 0.0,
        "depth_status": "live_liquid" if has_liquidity else "no_liquidity",
        "sqrt_price_x96": "0",
        "tick": 0,
        "liquidity_block_number": 1000,
        "evidence_source": "live_onchain",
        "balances": {"tvlUsd": 2000.0} if has_liquidity else {},
    }


def _seed(tmp_path, *, bad_pool: str | None = None, unresolved_middle_symbol: str | None = None):
    db = str(tmp_path / "report.sqlite3")
    ensure_business_schema(db)
    seed_chain_tokens(
        db,
        network=NETWORK,
        chain_id=CHAIN_ID,
        token_rows=[
            {"token_address": ETH, "token_symbol": "ETH", "token_decimals": 18, "is_native": 1},
            {"token_address": TZ, "token_symbol": "TZ", "token_decimals": 18},
            {"token_address": TB, "token_symbol": "TB", "token_decimals": 18},
        ],
    )
    yc_pool = _pool(P1, ETH, TZ)
    zc_pool = _pool(P2, TB, ETH)
    bridge_pool = _pool(P3, TZ, TB)
    raw_pools = [yc_pool, zc_pool, bridge_pool]
    if bad_pool:
        for pool in raw_pools:
            if pool["pool_id"] == bad_pool:
                pool.update(
                    {
                        "has_liquidity": 0,
                        "liquidity": "0",
                        "liquidity_index": 0,
                        "estimated_tvl_usd": 0,
                        "depth_status": "no_liquidity",
                        "evidence_source": "live_onchain",
                    }
                )
    upsert_v4_exchange_pools(
        db,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        pool_rows=raw_pools,
    )
    upsert_v4_liquidity_snapshots(
        db,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        rows=raw_pools,
    )
    replace_dex_candidate_tables(
        db,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        anchor_scope="yc_zc",
        anchor_symbol="ETH",
        tokens=[
            {
                "token_address": TZ,
                "pool_count": 1,
                "has_yc_optimal_pool": 1,
                "yc_optimal_pool_id": P1,
            },
            {
                "token_address": TB,
                "pool_count": 1,
                "has_zc_optimal_pool": 1,
                "zc_optimal_pool_id": P2,
            },
        ],
        pairs=[
            {
                "anchor_symbol": "ETH",
                "anchor_address": ETH,
                "counter_address": TZ,
                "direction_key": f"{ETH}:{TZ}",
                "pool_count": 1,
                "yc_optimal_pool_id": P1,
                "zc_optimal_pool_id": "",
            },
            {
                "anchor_symbol": "ETH",
                "anchor_address": ETH,
                "counter_address": TB,
                "direction_key": f"{ETH}:{TB}",
                "pool_count": 1,
                "yc_optimal_pool_id": "",
                "zc_optimal_pool_id": P2,
            },
        ],
        pools=raw_pools,
        recommendations=[],
    )
    directions = [
        {
            "anchor_symbol": "ETH",
            "token_x_address": TZ,
            "token_y_address": TB,
            "direction_key": f"{TZ}:{TB}",
            "all_pools": [dict(bridge_pool)],
        },
        {
            "anchor_symbol": "ETH",
            "token_x_address": TB,
            "token_y_address": TZ,
            "direction_key": f"{TB}:{TZ}",
            "all_pools": [dict(bridge_pool)],
        },
        {
            "anchor_symbol": "ETH",
            "token_x_address": ETH,
            "token_y_address": TZ,
            "direction_key": f"{ETH}:{TZ}",
            "all_pools": [dict(yc_pool)],
        },
    ]
    replace_market_observation_tables(
        db,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        anchor_scope="y_z",
        anchor_symbol="ETH",
        directions=directions,
        pools=raw_pools,
        liquidity=raw_pools,
    )
    resolved_path_symbols = ["ETH", "TZ", "TB", "ETH"]
    if unresolved_middle_symbol:
        resolved_path_symbols = ["ETH", unresolved_middle_symbol, "TB", "ETH"]
    loops = [
        {
            "loop_key": f"anchor:{ETH}:{TZ}",
            "anchor_address": ETH,
            "anchor_symbol": "ETH",
            "path_text": " -> ".join(resolved_path_symbols),
            "path_addresses_json": [ETH, TZ, TB, ETH],
            "path_symbols_json": resolved_path_symbols,
            "pool_ids_json": [P1, P3, P2],
        }
    ]
    hops = [
        {
            "loop_key": f"anchor:{ETH}:{TZ}",
            "hop_index": 1,
            "hop_name": "yc",
            "input_address": ETH,
            "output_address": TZ,
            "pool_id": P1,
        },
        {
            "loop_key": f"anchor:{ETH}:{TZ}",
            "hop_index": 2,
            "hop_name": "bridge",
            "input_address": TZ,
            "output_address": TB,
            "pool_id": P3,
        },
        {
            "loop_key": f"anchor:{ETH}:{TZ}",
            "hop_index": 3,
            "hop_name": "zc",
            "input_address": TB,
            "output_address": ETH,
            "pool_id": P2,
        },
    ]
    replace_v4_closed_loop_tables(
        db,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        market_id=MARKET_ID,
        loops=loops,
        hops=hops,
    )
    pools = {
        P1: yc_pool,
        P2: zc_pool,
        P3: bridge_pool,
    }
    route_pools = []
    for pool_id in (P1, P3, P2):
        pool = dict(pools[pool_id])
        route_pools.append(
            {
                "pool": pool_id,
                "poolId": pool_id,
                "hasLiquidity": not bad_pool or pool_id != bad_pool,
                "liquidity": pool["liquidity"],
                "liquidityIndex": pool["liquidity_index"],
                "estimatedTvlUsd": pool["estimated_tvl_usd"],
                "depthStatus": pool["depth_status"],
            }
        )
    save_dex_overview_snapshot(
        {
            "exists": True,
            "path": "sqlite_db",
            "source": "sqlite_db",
            "network": NETWORK,
            "chain_id": CHAIN_ID,
            "protocols": [PROTOCOL],
            "summary": {
                "closedLoopAnchorSymbols": ["ETH"],
                "closedLoopAnchorCount": 1,
            },
            "borrowableSymbols": ["ETH"],
            "closedLoopRouteSummaries": [
                {
                    "anchorSymbol": "ETH",
                    "counterSymbol": "TZ",
                    "pathSymbols": resolved_path_symbols,
                    "pathText": " -> ".join(resolved_path_symbols),
                    "routeAddresses": [ETH, TZ, TB, ETH],
                    "poolIds": [P1, P3, P2],
                    "routePools": route_pools,
                }
            ],
            "chainSpecificObservationPairs": [],
            "poolRegistry": {},
            "liquidPoolRegistry": {},
            "poolIndexVersion": {},
        },
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        database_url=db,
    )
    return db


def test_integrity_report_passes_when_every_loop_hop_is_confirmed(tmp_path):
    db = _seed(tmp_path)
    report = build_v4_integrity_report(
        db,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        market_id=MARKET_ID,
    )
    assert report["ok"] is True
    assert report["violationCount"] == 0
    assert report["legalAnchorSymbols"] == ("ETH", "WETH", "TZ", "USDE", "USDG")
    assert [stage["name"] for stage in report["stages"]] == [
        "pool_index",
        "candidate",
        "observation",
        "closed_loop",
        "overview_quote",
    ]
    pool_stage = report["stages"][0]
    assert pool_stage["stats"]["poolCount"] == 3
    assert pool_stage["stats"]["confirmedLiquidityCount"] == 3
    loop_stage = report["stages"][3]
    assert loop_stage["stats"]["loopCount"] == 1
    assert loop_stage["stats"]["confirmedLoopCount"] == 1
    overview_stage = report["stages"][4]
    assert overview_stage["stats"]["snapshotPresent"] is True
    assert overview_stage["stats"]["routeSummaryCount"] == 1
    assert overview_stage["stats"]["anchorSymbols"] == ["ETH"]


def test_integrity_report_fails_when_loop_hop_pool_loses_liquidity(tmp_path):
    db = _seed(tmp_path, bad_pool=P3)
    report = build_v4_integrity_report(
        db,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        market_id=MARKET_ID,
    )
    assert report["ok"] is False
    codes = [violation["code"] for violation in report["violations"]]
    assert "executable_pool_without_confirmed_liquidity" in codes
    loop_stage = report["stages"][3]
    assert loop_stage["stats"]["confirmedLoopCount"] == 0
    pool_stage = report["stages"][0]
    assert pool_stage["stats"]["confirmedLiquidityCount"] == 2
    assert pool_stage["stats"]["negativeEvidenceCount"] == 1


def test_integrity_report_flags_unresolved_placeholder_in_loop_path(tmp_path):
    db = _seed(tmp_path, unresolved_middle_symbol="0X60042B65")
    report = build_v4_integrity_report(
        db,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        market_id=MARKET_ID,
    )
    assert report["ok"] is False
    codes = [violation["code"] for violation in report["violations"]]
    assert "loop_path_contains_unresolved_symbol" in codes
    assert "overview_route_contains_unresolved_symbol" in codes


def test_integrity_report_flags_missing_overview_as_warning_only(tmp_path):
    db = _seed(tmp_path)
    from core.db.storage_dex_surface_snapshot import clear_dex_surface_snapshots

    clear_dex_surface_snapshots(
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        database_url=db,
    )
    report = build_v4_integrity_report(
        db,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        market_id=MARKET_ID,
    )
    assert report["ok"] is True
    assert any(
        warning.get("code") == "overview_snapshot_missing"
        for warning in report["warnings"]
    )
    assert report["stages"][4]["stats"]["snapshotPresent"] is False
