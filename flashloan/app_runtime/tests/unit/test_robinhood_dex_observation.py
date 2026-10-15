from dex.robinhood_dex_panel import (
    load_robinhood_sqlite_market_snapshot,
    robinhood_dex_observation_payload,
    robinhood_dex_pool_observation_payload,
    robinhood_dex_readiness_payload,
)
from core.db.database_path import absolute_database_path
from core.db.storage_chain_core import seed_chain_tokens
from core.db.storage_chain_markets import replace_current_dex_arbitrage_candidates
from core.db.storage_schema import ensure_database_schema
from core.db.storage_v4_exchange import (
    upsert_v4_exchange_pools,
    upsert_v4_liquidity_snapshots,
)
from core.kv_rows import collection_key
from core.local_kv import put


ROBINHOOD_MARKET_ID = "dex-arbitrage-4663-uniswap-v4"
WETH = "0x7943e237c7f95da44e0301572d358911207852fa"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
USDE = "0x5037f181818c8b6f2f1f1f1f1f1f1f1f1f1f1f1f1f"
POOL_IDS = ("0x" + "a" * 64, "0x" + "b" * 64, "0x" + "c" * 64)


def _seed_sqlite(tmp_path):
    db = str(absolute_database_path(tmp_path / "store.sqlite3"))
    ensure_database_schema(db)
    seed_chain_tokens(
        db,
        network="robinhood",
        chain_id=4663,
        token_rows=[
            {
                "token_address": WETH,
                "token_symbol": "WETH",
                "token_decimals": 18,
            },
            {
                "token_address": USDG,
                "token_symbol": "USDG",
                "token_decimals": 6,
            },
            {
                "token_address": USDE,
                "token_symbol": "USDe",
                "token_decimals": 18,
            },
        ],
    )
    upsert_v4_exchange_pools(
        db,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        pool_rows=[
            {
                "pool_id": POOL_IDS[0],
                "token0": WETH,
                "token1": USDG,
                "fee": 500,
                "tick_spacing": 10,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
            {
                "pool_id": POOL_IDS[1],
                "token0": USDG,
                "token1": USDE,
                "fee": 100,
                "tick_spacing": 1,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
            {
                "pool_id": POOL_IDS[2],
                "token0": USDE,
                "token1": WETH,
                "fee": 3000,
                "tick_spacing": 60,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
        ],
    )
    upsert_v4_liquidity_snapshots(
        db,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        rows=[
            {
                "pool_id": POOL_IDS[0],
                "has_liquidity": 1,
                "liquidity": "1000",
                "liquidity_index": 1000.0,
                "estimated_tvl_usd": 1000.0,
                "sqrt_price_x96": "1",
                "liquidity_block_number": 42,
            },
            {
                "pool_id": POOL_IDS[1],
                "has_liquidity": 1,
                "liquidity": "2000",
                "liquidity_index": 2000.0,
                "estimated_tvl_usd": 2000.0,
                "sqrt_price_x96": "2",
                "liquidity_block_number": 42,
            },
            {
                "pool_id": POOL_IDS[2],
                "has_liquidity": 1,
                "liquidity": "3000",
                "liquidity_index": 3000.0,
                "estimated_tvl_usd": 3000.0,
                "sqrt_price_x96": "3",
                "liquidity_block_number": 42,
            },
        ],
    )
    replace_current_dex_arbitrage_candidates(
        db,
        market_id=ROBINHOOD_MARKET_ID,
        candidates=[
            {
                "market_id": ROBINHOOD_MARKET_ID,
                "chain_id": 4663,
                "route_key": "USDG-WETH-USDG",
                "route_symbols": ["USDG", "WETH", "USDG"],
                "pool_addresses": list(POOL_IDS),
                "status": "observed",
                "evidence": {
                    "pools": [
                        {"eventVerified": True, "poolCodeVerified": True, "poolStateVerified": True},
                        {"eventVerified": True, "poolCodeVerified": True, "poolStateVerified": True},
                        {"eventVerified": True, "poolCodeVerified": True, "poolStateVerified": True},
                    ]
                },
            }
        ],
    )
    return db


def _healthy_rpc(*_args, **_kwargs):
    return {
        "ok": True,
        "checks": [{"name": "eth_blockNumber", "value": 99}],
    }


def test_robinhood_observation_payload_is_sqlite_only(tmp_path, monkeypatch):
    db = _seed_sqlite(tmp_path)
    monkeypatch.setattr(
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
        _healthy_rpc,
    )

    payload = robinhood_dex_observation_payload(
        database_url=db,
    )

    assert payload["candidateSource"] == "database"
    assert payload["dataSource"]["source"] == "database"
    assert payload["evidence"]["source"] == "sqlite_db"
    assert payload["chainStatus"]["ok"] is True
    assert payload["summary"]["executionEnabled"] is False
    assert payload["summary"]["deploymentLevel"] == "framework_and_observation_shadow"
    assert payload["summary"]["poolSource"] == "sqlite_db"
    assert payload["observationStatus"] == "pools_available"
    assert len(payload["pools"]) == 3
    assert payload["pools"][0]["source"] == "sqlite_db"
    assert len(payload["candidates"]) == 1
    assert "candidateCachePath" not in payload
    assert payload["databasePath"] == db


def test_robinhood_observation_rejects_wrong_market_scope():
    try:
        robinhood_dex_observation_payload(market_id="bnb-venus-core", chain_id=56, run_rpc_probe=False)
    except ValueError as exc:
        assert "belongs to" in str(exc) or "requires" in str(exc)
    else:
        raise AssertionError("wrong market scope must be rejected")


def test_robinhood_observation_rejects_robinhood_testnet_scope():
    try:
        robinhood_dex_observation_payload(
            market_id="robinhood-testnet-dex-paused",
            chain_id=46630,
            run_rpc_probe=False,
        )
    except ValueError as exc:
        assert "Robinhood DEX observation requires the dex-arbitrage-4663-uniswap-v4 market scope" in str(exc)
    else:
        raise AssertionError("Robinhood testnet must not use the Robinhood mainnet DEX observation handler")


def test_robinhood_observation_explains_empty_database(tmp_path, monkeypatch):
    db = str(absolute_database_path(tmp_path / "empty.sqlite3"))
    ensure_database_schema(db)
    monkeypatch.setattr(
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
        lambda *_args, **_kwargs: {"ok": True, "checks": []},
    )

    payload = robinhood_dex_observation_payload(
        database_url=db,
    )

    assert payload["observationStatus"] == "awaiting_discovery_evidence"
    assert payload["summary"]["candidateCount"] == 0
    assert payload["observationDiagnostics"]["rpcHealthy"] is True
    assert payload["observationDiagnostics"]["evidenceAvailable"] is False
    assert payload["dataSource"]["fallback_reason"] == "database_empty"
    assert "Refresh the V4 pool/liquidity tables" in payload["observationDiagnostics"]["nextAction"]


def test_robinhood_readiness_payload_uses_sqlite_discovery(tmp_path, monkeypatch):
    db = _seed_sqlite(tmp_path)
    monkeypatch.setattr(
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
        _healthy_rpc,
    )

    payload = robinhood_dex_readiness_payload(
        database_url=db,
    )

    discovery = payload["evidence"]["discoveryReport"]
    assert payload["evidence"]["source"] == "sqlite_db"
    assert discovery["factoryCodeVerified"] is True
    assert discovery["poolCount"] == 3
    discovery_stage = next(stage for stage in payload["deploymentStatus"]["stages"] if stage["key"] == "dex_discovery")
    assert discovery_stage["status"] == "ready"
    assert payload["deploymentStatus"]["deploymentComplete"] is False
    assert payload["executionEnabled"] is False
    assert payload["broadcastEnabled"] is False


def test_robinhood_observation_publishes_pnl_from_sqlite_kv_after_pools(tmp_path, monkeypatch):
    db = _seed_sqlite(tmp_path)
    put(
        db,
        "robinhood_market_snapshot",
        collection_key("robinhood", 4663),
        {
            "network": "robinhood",
            "chainId": 4663,
            "blockNumber": 123,
            "bestRoute": {"grossProfit": "-0.2"},
        },
    )
    monkeypatch.setattr(
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
        lambda *_args, **_kwargs: {"ok": True, "checks": [{"name": "eth_blockNumber", "value": 123}]},
    )

    payload = robinhood_dex_observation_payload(
        database_url=db,
    )

    assert payload["observationStatus"] == "pools_available"
    assert payload["summary"]["marketSnapshotAvailable"] is True
    assert payload["marketSnapshot"]["blockNumber"] == 123
    assert payload["observationDiagnostics"]["marketSnapshotBlock"] == 123

    snapshot = load_robinhood_sqlite_market_snapshot(
        db,
        network="robinhood",
        chain_id=4663,
    )
    assert snapshot is not None
    assert snapshot["blockNumber"] == 123


def test_robinhood_observation_suppresses_pnl_without_pool_rows(tmp_path, monkeypatch):
    db = str(absolute_database_path(tmp_path / "no-pools.sqlite3"))
    ensure_database_schema(db)
    put(
        db,
        "robinhood_market_snapshot",
        collection_key("robinhood", 4663),
        {
            "network": "robinhood",
            "chainId": 4663,
            "blockNumber": 123,
            "bestRoute": {"grossProfit": "-0.2"},
        },
    )
    monkeypatch.setattr(
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
        lambda *_args, **_kwargs: {"ok": True, "checks": []},
    )

    payload = robinhood_dex_observation_payload(
        database_url=db,
    )

    assert payload["observationStatus"] == "awaiting_discovery_evidence"
    assert payload["summary"]["marketSnapshotAvailable"] is False
    assert payload["marketSnapshot"] is None
    assert payload["observationDiagnostics"]["marketSnapshotSuppressed"] is True
    assert payload["observationDiagnostics"]["bestRouteGrossProfit"] is None


def test_pool_observation_payload_exposes_only_pool_surface(tmp_path, monkeypatch):
    db = _seed_sqlite(tmp_path)
    monkeypatch.setattr(
        "dex.robinhood_dex_panel.probe_robinhood_rpc",
        _healthy_rpc,
    )

    payload = robinhood_dex_pool_observation_payload(
        database_url=db,
    )

    assert payload["summary"]["poolCount"] == 3
    assert len(payload["pools"]) == 3
    assert payload["evidence"]["source"] == "sqlite_db"
    assert "candidates" not in payload
    assert "marketSnapshot" not in payload
    assert payload["summary"]["executionEnabled"] is False
