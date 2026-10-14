import json


from core.db.storage_business_schema import ensure_business_schema
from core.db.storage_chain_core import seed_chain_tokens
from core.db.storage_dex_candidate import replace_dex_candidate_tables
from core.db.storage_market_observation import replace_market_observation_tables
from core.db.storage_v4_closed_loop import replace_v4_closed_loop_tables
from dex.intent_trade.chains.robinhood_common.market_observation_core import (
    db_closed_loop_sequences,
)


ETH = "0x0000000000000000000000000000000000000000"
TZ = "0x0000000000000000000000000000000000000001"
TB = "0x0000000000000000000000000000000000000002"


def _pool(pool_id, token0, token1, *, liquid=True):
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
        "has_liquidity": 1 if liquid else 0,
        "liquidity": "1000000000000000000000" if liquid else "0",
        "liquidity_index": 1000.0 if liquid else 0.0,
        "estimated_tvl_usd": 2000.0 if liquid else 0.0,
        "depth_status": "live_liquid" if liquid else "no_liquidity",
        "sqrt_price_x96": "0",
        "tick": 0,
        "balances_json": json.dumps({"tvlUsd": 2000.0}) if liquid else "{}",
    }


def _seed_db(tmp_path, *, stored_pool_liquid=True, backup_pool=None):
    db = str(tmp_path / "closed-loop.sqlite3")
    ensure_business_schema(db)
    seed_chain_tokens(
        db,
        network="robinhood-testnet",
        chain_id=46630,
        token_rows=[
            {"token_address": ETH, "token_symbol": "ETH", "token_decimals": 18, "is_native": 1},
            {"token_address": TZ, "token_symbol": "TZ", "token_decimals": 18},
            {"token_address": TB, "token_symbol": "TB", "token_decimals": 18},
        ],
    )

    stored_yc = _pool("0x" + "0" * 63 + "1", ETH, TZ, liquid=stored_pool_liquid)
    stored_zc = _pool("0x" + "0" * 63 + "2", ETH, TB)
    bridge = _pool("0x" + "0" * 63 + "3", TZ, TB)
    extra = _pool("0x" + "0" * 63 + "4", ETH, TZ)
    candidate_pools = [stored_yc, stored_zc, bridge, extra]
    if backup_pool is not None:
        candidate_pools.append(backup_pool)

    yc_backups = [backup_pool["pool_id"]] if backup_pool is not None else []
    candidate_pairs = [
        {
            "anchor_symbol": "ETH",
            "counter_symbol": "TZ",
            "anchor_address": ETH,
            "counter_address": TZ,
            "address_pair": f"{ETH}:{TZ}",
            "direction_key": f"{ETH}:{TZ}",
            "pool_count": 1,
            "yc_optimal_pool_id": stored_yc["pool_id"],
            "zc_optimal_pool_id": "",
            "yc_backup_pool_ids": yc_backups,
            "zc_backup_pool_ids": [],
        },
        {
            "anchor_symbol": "ETH",
            "counter_symbol": "TB",
            "anchor_address": TB,
            "counter_address": ETH,
            "address_pair": f"{TB}:{ETH}",
            "direction_key": f"{TB}:{ETH}",
            "pool_count": 1,
            "yc_optimal_pool_id": "",
            "zc_optimal_pool_id": stored_zc["pool_id"],
            "yc_backup_pool_ids": [],
            "zc_backup_pool_ids": [],
        },
    ]
    replace_dex_candidate_tables(
        db,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        anchor_scope="yc_zc",
        anchor_symbol="ETH",
        tokens=[],
        pairs=candidate_pairs,
        pools=candidate_pools,
        recommendations=[],
    )

    directions = [
        {
            "anchor_symbol": "ETH",
            "token_x_address": TZ,
            "token_y_address": TB,
            "direction_key": f"{TZ}:{TB}",
            "all_pools": [dict(bridge)],
        }
    ]
    replace_market_observation_tables(
        db,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        anchor_scope="y_z",
        anchor_symbol="ETH",
        directions=directions,
        pools=[bridge],
        liquidity=[bridge],
    )

    loops = [
        {
            "loop_key": f"anchor:{ETH}:{TZ}",
            "anchor_address": ETH,
            "anchor_symbol": "ETH",
            "path_text": "ETH -> TZ -> TB -> ETH",
            "path_addresses_json": json.dumps([ETH, TZ, TB, ETH]),
            "path_symbols_json": json.dumps(["ETH", "TZ", "TB", "ETH"]),
            "pool_ids_json": json.dumps([bridge["pool_id"]]),
        }
    ]
    hops = [
        {
            "loop_key": f"anchor:{ETH}:{TZ}",
            "hop_index": 1,
            "hop_name": "yc",
            "input_address": ETH,
            "output_address": TZ,
            "pool_id": stored_yc["pool_id"],
        },
        {
            "loop_key": f"anchor:{ETH}:{TZ}",
            "hop_index": 2,
            "hop_name": "bridge",
            "input_address": TZ,
            "output_address": TB,
            "pool_id": bridge["pool_id"],
        },
        {
            "loop_key": f"anchor:{ETH}:{TZ}",
            "hop_index": 3,
            "hop_name": "zc",
            "input_address": TB,
            "output_address": ETH,
            "pool_id": stored_zc["pool_id"],
        },
    ]
    replace_v4_closed_loop_tables(
        db,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
        loops=loops,
        hops=hops,
    )
    return db


def test_db_closed_loop_sequences_uses_recommended_scope_without_full_refresh(tmp_path):
    db = _seed_db(tmp_path)

    sequences = db_closed_loop_sequences(
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
        kind="robinhood_testnet_closed_loop",
        database_url=db,
    )

    assert len(sequences) == 1
    sequence = sequences[0]
    yc = next(hop for hop in sequence["hopCandidates"] if hop["direction"] == "yc")
    assert yc["preferredPoolId"] == "0x" + "0" * 63 + "1"
    assert yc["poolCount"] == 1
    assert yc["dynamicSelectionReason"] == "stored_primary_usable"
    assert "0x" + "0" * 63 + "4" not in yc["poolIds"]


def test_db_closed_loop_sequences_swaps_backup_only_once(tmp_path):
    backup = _pool("0x" + "0" * 63 + "5", ETH, TZ)
    db = _seed_db(tmp_path, stored_pool_liquid=False, backup_pool=backup)

    sequences = db_closed_loop_sequences(
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
        kind="robinhood_testnet_closed_loop",
        database_url=db,
    )

    assert len(sequences) == 1
    sequence = sequences[0]
    yc = next(hop for hop in sequence["hopCandidates"] if hop["direction"] == "yc")
    assert yc["preferredPoolId"] == backup["pool_id"]
    assert yc["dynamicSelectionReason"] == "backup_replacement"
    assert yc["poolCount"] == 1


def test_db_closed_loop_sequences_does_not_force_backup_when_only_one_pool(tmp_path):
    db = _seed_db(tmp_path)

    sequences = db_closed_loop_sequences(
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
        kind="robinhood_testnet_closed_loop",
        database_url=db,
    )

    assert len(sequences) == 1
    sequence = sequences[0]
    yc = next(hop for hop in sequence["hopCandidates"] if hop["direction"] == "yc")
    assert yc["preferredPoolId"] == "0x" + "0" * 63 + "1"
    assert yc["poolIds"] == ["0x" + "0" * 63 + "1"]
    assert yc["backupPoolIds"] == []
    assert yc["poolCount"] == 1


def test_mainnet_observation_skips_legacy_enumeration_when_db_closed_loops_exist(monkeypatch):
    import dex.intent_trade.chains.robinhood.market_observation as mainnet_observation

    db_sequence = {
        "kind": "robinhood_mainnet_closed_loop",
        "source": "v4_closed_loop_db",
        "anchorSymbol": "ETH",
        "bridgeSymbol": "USDG",
        "counterSymbol": "USDE",
        "pathSymbols": ["ETH", "USDG", "USDE", "ETH"],
        "pathText": "ETH -> USDG -> USDE -> ETH",
        "routePools": [],
    }
    monkeypatch.setattr(mainnet_observation, "_mainnet_closed_loop_sequences", lambda: [db_sequence])

    def fail_legacy_enumeration(*args, **kwargs):
        raise AssertionError("legacy YC x ZC enumeration must be skipped when DB closed loops exist")

    monkeypatch.setattr(mainnet_observation, "_mainnet_observation_sequences", fail_legacy_enumeration)
    monkeypatch.setattr(mainnet_observation, "_mainnet_validate_anchor_groups", lambda anchor_groups: {})
    monkeypatch.setattr(mainnet_observation, "_mainnet_db_intermediate_pool_reports", lambda anchor_groups, pools: ([], []))
    monkeypatch.setattr(mainnet_observation, "_mainnet_candidate_yc_zc_tokens", lambda group: ([], []))
    monkeypatch.setattr(mainnet_observation, "_mainnet_eth_pair_token_count", lambda: 0)
    monkeypatch.setattr(mainnet_observation, "_load_mainnet_v4_index", lambda: ({}, {"poolCount": 0}))
    monkeypatch.setattr(mainnet_observation, "_mainnet_configured_anchor_addresses", lambda: set())
    monkeypatch.setattr(mainnet_observation, "_anchor_neighbors", lambda pools, anchor_addresses: {})
    monkeypatch.setattr(mainnet_observation, "_active_pool_ids", lambda pools: [])

    payload = mainnet_observation._mainnet_fund_anchor_observation(
        [],
        anchor_groups=[{"anchorSymbol": "ETH", "anchorAddresses": ["0x0000000000000000000000000000000000000000"]}],
    )

    assert payload["routeCount"] == 1
    assert payload["closedLoopSequences"] == [db_sequence]
    assert payload["observationSequences"] == [db_sequence]
    assert len(payload["closedLoopRoutes"]) == 1
    assert payload["closedLoopRoutes"][0]["source"] == "v4_closed_loop_db"


def test_mainnet_observation_returns_empty_without_legacy_enumeration_when_db_empty(monkeypatch):
    import dex.intent_trade.chains.robinhood.market_observation as mainnet_observation

    monkeypatch.setattr(mainnet_observation, "_mainnet_closed_loop_sequences", lambda: [])

    def fail_legacy_enumeration(*args, **kwargs):
        raise AssertionError("legacy YC x ZC enumeration must never run for mainnet observation")

    monkeypatch.setattr(mainnet_observation, "_mainnet_observation_sequences", fail_legacy_enumeration)
    monkeypatch.setattr(mainnet_observation, "_mainnet_validate_anchor_groups", lambda anchor_groups: {})
    monkeypatch.setattr(mainnet_observation, "_mainnet_db_intermediate_pool_reports", lambda anchor_groups, pools: ([], []))
    monkeypatch.setattr(mainnet_observation, "_mainnet_candidate_yc_zc_tokens", lambda group: ([], []))
    monkeypatch.setattr(mainnet_observation, "_mainnet_eth_pair_token_count", lambda: 0)
    monkeypatch.setattr(mainnet_observation, "_load_mainnet_v4_index", lambda: ({}, {"poolCount": 0}))
    monkeypatch.setattr(mainnet_observation, "_mainnet_configured_anchor_addresses", lambda: set())
    monkeypatch.setattr(mainnet_observation, "_anchor_neighbors", lambda pools, anchor_addresses: {})
    monkeypatch.setattr(mainnet_observation, "_active_pool_ids", lambda pools: [])

    payload = mainnet_observation._mainnet_fund_anchor_observation(
        [],
        anchor_groups=[{"anchorSymbol": "ETH", "anchorAddresses": ["0x0000000000000000000000000000000000000000"]}],
    )

    assert payload["routeCount"] == 0
    assert payload["closedLoopSequences"] == []
    assert payload["observationSequences"] == []
    assert payload["closedLoopRoutes"] == []
