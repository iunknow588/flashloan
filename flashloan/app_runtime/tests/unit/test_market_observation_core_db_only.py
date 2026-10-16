import json


from core.db.storage_business_schema import ensure_business_schema
from core.db.storage_chain_core import seed_chain_tokens
from core.db.storage_dex_candidate import replace_dex_candidate_tables
from core.db.storage_market_observation import replace_market_observation_tables
from core.db.storage_v4_closed_loop import replace_v4_closed_loop_tables
from core.db.storage_v4_exchange import (
    upsert_v4_exchange_pools,
    upsert_v4_liquidity_snapshots,
)
from dex.intent_trade.chains.robinhood_common.market_observation_core import (
    db_closed_loop_sequences,
)


ETH = "0x0000000000000000000000000000000000000000"
TZ = "0x0000000000000000000000000000000000000001"
TB = "0x0000000000000000000000000000000000000002"


def _real_runtime_database() -> str | None:
    import os
    from pathlib import Path

    env_path = os.getenv("FLASHLOAN_KV_PATH", "").strip()
    if env_path:
        candidate = Path(env_path)
    else:
        candidate = (
            Path(__file__).resolve().parents[3]
            / "src"
            / "runtime"
            / "local_store.sqlite3"
        )
    if not candidate.exists():
        return None
    try:
        candidate.resolve(strict=True)
    except OSError:
        return None
    return str(candidate)


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


def test_db_closed_loop_sequences_uses_exchange_live_evidence_when_derived_rows_are_weak(tmp_path):
    import sqlite3

    db = _seed_db(tmp_path)
    yc_pool_id = "0x" + "0" * 63 + "1"
    con = sqlite3.connect(db)
    con.execute(
        """
        UPDATE dex_candidate_pools
        SET has_liquidity = 0, liquidity = '0', liquidity_index = 0.0,
            estimated_tvl_usd = 0.0, depth_status = 'no_liquidity',
            balances_json = '{}'
        WHERE network = 'robinhood-testnet' AND chain_id = 46630
          AND protocol = 'uniswap_v4' AND pool_id = ?
        """,
        (yc_pool_id,),
    )
    con.commit()
    con.close()

    upsert_v4_liquidity_snapshots(
        db,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        rows=[
            {
                "pool_id": yc_pool_id,
                "has_liquidity": 1,
                "liquidity": "5962221889878846",
                "liquidity_index": 0.00596222,
                "estimated_tvl_usd": 0.088,
                "depth_status": "thin",
                "evidence_source": "pool_manager_extsload_live_state_batch",
            }
        ],
    )

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
    assert yc["preferredPoolId"] == yc_pool_id
    assert yc["pools"][0]["evidenceSource"] == "pool_manager_extsload_live_state_batch"


def test_db_closed_loop_sequences_restores_pool_key_from_exchange_pools(tmp_path):
    import sqlite3

    db = _seed_db(tmp_path)
    stored_yc = _pool("0x" + "0" * 63 + "1", ETH, TZ)
    stored_zc = _pool("0x" + "0" * 63 + "2", ETH, TB)
    bridge = _pool("0x" + "0" * 63 + "3", TZ, TB)
    for pool in (stored_yc, stored_zc, bridge):
        upsert_v4_exchange_pools(
            db,
            network="robinhood-testnet",
            chain_id=46630,
            protocol="uniswap_v4",
            pool_rows=[
                {
                    "pool_id": pool["pool_id"],
                    "pool_manager": pool["pool_manager"],
                    "pool_key": pool["pool_key"],
                    "token0": pool["token0"],
                    "token1": pool["token1"],
                    "fee": pool["fee"],
                    "tick_spacing": pool["tick_spacing"],
                    "hooks": pool["hooks"],
                }
            ],
        )

    # Derived candidate/observation rows can lose the raw pool key. The loop
    # assembly must restore it from v4_exchange_pools instead of keeping an
    # empty poolKey that quote/route surfaces later reject.
    con = sqlite3.connect(db)
    con.execute(
        """
        UPDATE dex_candidate_pools
        SET pool_key_json = NULL
        WHERE network = 'robinhood-testnet' AND chain_id = 46630
          AND protocol = 'uniswap_v4'
        """
    )
    con.execute(
        """
        UPDATE market_observation_intermediate_pools
        SET pool_key_json = NULL
        WHERE network = 'robinhood-testnet' AND chain_id = 46630
          AND protocol = 'uniswap_v4'
        """
    )
    con.commit()
    con.close()

    sequences = db_closed_loop_sequences(
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
        kind="robinhood_testnet_closed_loop",
        database_url=db,
    )

    assert len(sequences) == 1
    for step in sequences[0]["routePools"]:
        pool_key = step["poolKey"]
        assert isinstance(pool_key, dict)
        assert pool_key.get("currency0")
        assert pool_key.get("currency1")
        assert pool_key.get("fee") == 3000
        assert str(pool_key.get("hooks") or "").lower() == ETH


def test_real_runtime_db_directed_closed_loop_contract():
    import sqlite3
    from pathlib import Path

    db = _real_runtime_database()
    if db is None:
        import pytest

        pytest.skip("real runtime sqlite database is not present")
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    cur = con.cursor()
    try:
        mainnet_rows = cur.execute(
            """
            SELECT loop_key, path_symbols_json
            FROM v4_closed_loops
            WHERE network = 'robinhood' AND chain_id = 4663
              AND protocol = 'uniswap_v4'
            ORDER BY loop_key
            """
        ).fetchall()
        # The DB-first builder recovered additional mainnet loops after the
        # liquidity gate stopped rejecting large liquidity-index pools with a
        # small USD depth.  Keep the assertion as a lower bound plus the
        # liquidity invariant instead of a hard-coded historical count.
        assert len(mainnet_rows) >= 22
        assert all("0X" not in str(row[1]).upper() for row in mainnet_rows)
        symbols = {tuple(json.loads(row[1])) for row in mainnet_rows}
        assert len(symbols) == len(mainnet_rows)
        reversed_symbols = {
            (path[0], path[2], path[1], path[0])
            for path in symbols
            if len(path) == 4
        }
        assert reversed_symbols <= symbols

        hop_rows = cur.execute(
            """
            SELECT h.hop_name, h.pool_id
            FROM v4_closed_loops c
            JOIN v4_closed_loop_hops h
              ON h.loop_key = c.loop_key
             AND h.market_id = c.market_id
            WHERE c.network = 'robinhood' AND c.chain_id = 4663
              AND c.protocol = 'uniswap_v4'
            """
        ).fetchall()
        assert len(hop_rows) == len(mainnet_rows) * 3
        assert all(str(row[0]) in {"yc", "y-z", "zc"} for row in hop_rows)
        assert all(str(row[1]).strip() for row in hop_rows)
        liquid_hops = cur.execute(
            """
            SELECT COUNT(*)
            FROM v4_closed_loops c
            JOIN v4_closed_loop_hops h
              ON h.loop_key = c.loop_key
             AND h.market_id = c.market_id
            JOIN v4_exchange_pool_liquidity_snapshots s
              ON s.network = h.network AND s.chain_id = h.chain_id
             AND s.protocol = h.protocol AND s.pool_id = h.pool_id
            WHERE c.network = 'robinhood' AND c.chain_id = 4663
              AND c.protocol = 'uniswap_v4' AND s.has_liquidity = 1
            """
        ).fetchone()[0]
        assert liquid_hops == len(hop_rows)

        testnet_rows = cur.execute(
            """
            SELECT loop_key, anchor_symbol, path_symbols_json
            FROM v4_closed_loops
            WHERE network = 'robinhood-testnet' AND chain_id = 46630
              AND protocol = 'uniswap_v4'
            ORDER BY loop_key
            """
        ).fetchall()
        assert len(testnet_rows) > 0
        assert all("0X" not in str(row[2]).upper() for row in testnet_rows)
        testnet_paths = [tuple(json.loads(row[2])) for row in testnet_rows]
        assert all(len(path) >= 4 and path[0] == path[-1] for path in testnet_paths)
        legal_endpoints = {
            "ETH", "WETH", "USDE", "USDG", "TZ",
            "AMD", "AMZN", "NFLX", "PLTR", "TSLA",
        }
        assert all(path[0] in legal_endpoints for path in testnet_paths)
        tz_paths = {
            tuple(path)
            for row in testnet_rows
            if row[1] == "TZ"
            for path in [tuple(json.loads(row[2]))]
        }
        assert ("TZ", "TA", "TB", "TZ") in tz_paths
        assert ("TZ", "TB", "TA", "TZ") in tz_paths

        testnet_hop_rows = cur.execute(
            """
            SELECT h.hop_name, h.pool_id
            FROM v4_closed_loops c
            JOIN v4_closed_loop_hops h
              ON h.loop_key = c.loop_key
             AND h.market_id = c.market_id
            WHERE c.network = 'robinhood-testnet' AND c.chain_id = 46630
              AND c.protocol = 'uniswap_v4'
            """
        ).fetchall()
        assert len(testnet_hop_rows) == len(testnet_rows) * 3
        assert all(str(row[0]) in {"yc", "y-z", "zc"} for row in testnet_hop_rows)
        assert all(str(row[1]).strip() for row in testnet_hop_rows)
        liquid_testnet_hops = cur.execute(
            """
            SELECT COUNT(*)
            FROM v4_closed_loops c
            JOIN v4_closed_loop_hops h
              ON h.loop_key = c.loop_key
             AND h.market_id = c.market_id
            JOIN v4_exchange_pool_liquidity_snapshots s
              ON s.network = h.network AND s.chain_id = h.chain_id
             AND s.protocol = h.protocol AND s.pool_id = h.pool_id
            WHERE c.network = 'robinhood-testnet' AND c.chain_id = 46630
              AND c.protocol = 'uniswap_v4' AND s.has_liquidity = 1
            """
        ).fetchone()[0]
        assert liquid_testnet_hops == len(testnet_hop_rows)
    finally:
        con.close()


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
