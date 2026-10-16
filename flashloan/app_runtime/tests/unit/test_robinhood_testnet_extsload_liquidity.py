"""Regression tests for testnet V4 extsload liquidity evidence and tri-state DB storage.

The historical testnet ETH loops disappeared because liquidity evidence was
derived from Swap logs while initialized pools without recent swaps stayed
``has_liquidity=0``. These tests lock in the fix: live PoolManager extsload is
authoritative, read failures stay unknown, and SQLite preserves known evidence
instead of letting stale/swap-only rows turn real depth into false negatives.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.db.import_existing_json import import_existing_json
from core.db.storage_schema import ensure_database_schema
from core.db.storage_v4_exchange import (
    load_v4_pool_liquidity,
    upsert_v4_liquidity_snapshots,
)
from dex.intent_trade.chains.robinhood import v4_pool_index as mainnet_pool_index
from dex.intent_trade.chains.robinhood_testnet import v4_pool_liquidity as testnet_liquidity
from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
    ROBINHOOD_TESTNET_V4_POOL_MANAGER,
    ZERO_ADDRESS,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _pool(pool_id: str, **overrides: object) -> dict:
    row = {
        "protocol": "uniswap_v4",
        "poolId": pool_id,
        "poolManager": ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        "token0": ZERO_ADDRESS,
        "token1": "0x" + "11" * 20,
        "token0Symbol": "ETH",
        "token1Symbol": "TOKEN",
        "token0Decimals": 18,
        "token1Decimals": 18,
        "fee": 3000,
        "tickSpacing": 60,
        "hooks": ZERO_ADDRESS,
        "sqrtPriceX96": str(2**96),
        "tick": 0,
    }
    row.update(overrides)
    return row


def test_apply_extsload_liquidity_marks_real_depth_and_preserves_unknown_errors(monkeypatch):
    liquid_id = "0x" + "aa" * 32
    zero_id = "0x" + "bb" * 32
    failed_id = "0x" + "cc" * 32
    pools = [
        _pool(liquid_id),
        _pool(zero_id),
        _pool(failed_id, hasLiquidity=True),
    ]

    def fake_batch(**kwargs):
        return {
            liquid_id: {
                "sqrtPriceX96": str(2**96),
                "tick": 0,
                "liquidity": str(10**18),
                "blockNumber": 134_757_678,
                "source": "pool_manager_extsload_live_state_batch",
            },
            zero_id: {
                "error": "robinhood testnet pool manager live liquidity is zero",
                "source": "pool_manager_extsload_live_state_batch",
            },
            failed_id: {
                "error": "RPC timeout",
                "source": "pool_manager_extsload_live_state_batch",
            },
        }

    monkeypatch.setattr(testnet_liquidity, "_batch_read_live_pool_states", fake_batch)
    activity = testnet_liquidity._apply_extsload_liquidity(
        pools,
        w3=object(),
        rpc_url="https://fake.example",
        pool_manager=ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        batch_size=2,
    )

    liquid = next(row for row in pools if row["poolId"] == liquid_id)
    zero = next(row for row in pools if row["poolId"] == zero_id)
    failed = next(row for row in pools if row["poolId"] == failed_id)
    assert liquid["hasLiquidity"] is True
    assert liquid["liquidity"] == str(10**18)
    assert liquid["liquidityIndex"] > 0
    assert liquid["status"] == "initialized_pool_with_liquidity"
    assert zero["hasLiquidity"] is False
    assert zero["depthStatus"] == "no_liquidity"
    assert zero["liquidityError"] is None
    assert failed["hasLiquidity"] is True
    assert failed["depthStatus"] == "liquidity_read_failed"
    assert "RPC timeout" in failed["liquidityError"]
    assert activity["liquidityMarkedPoolCount"] == 1
    assert activity["liquidityErrorCount"] == 1
    assert activity["liquidityPoolIds"] == [liquid_id]


def test_upsert_liquidity_snapshots_treats_unknown_as_null_and_preserves_known(tmp_path):
    database = str(tmp_path / "local.sqlite3")
    ensure_database_schema(database)
    pool_id = "0x" + "dd" * 32

    upsert_v4_liquidity_snapshots(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        rows=[{"pool_id": pool_id, "liquidity": ""}],
    )
    row = load_v4_pool_liquidity(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
    )[0]
    assert row["has_liquidity"] is None

    upsert_v4_liquidity_snapshots(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        rows=[
            {
                "pool_id": pool_id,
                "has_liquidity": True,
                "liquidity": str(10**18),
                "liquidity_index": 1.0,
                "estimated_tvl_usd": 6000.0,
            }
        ],
    )
    row = load_v4_pool_liquidity(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
    )[0]
    assert row["has_liquidity"] == 1
    assert row["liquidity"] == str(10**18)

    # A later unknown row must not erase the explicit true evidence.
    upsert_v4_liquidity_snapshots(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        rows=[{"pool_id": pool_id, "liquidity": ""}],
    )
    row = load_v4_pool_liquidity(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
    )[0]
    assert row["has_liquidity"] == 1

    # A weak false row with no live evidence must not overwrite confirmed
    # evidence. Only an explicit live-state read may prove the pool is empty.
    upsert_v4_liquidity_snapshots(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        rows=[{"pool_id": pool_id, "has_liquidity": False, "liquidity": ""}],
    )
    row = load_v4_pool_liquidity(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
    )[0]
    assert row["has_liquidity"] == 1

    # Explicit live-state evidence is authoritative for a true zero.
    upsert_v4_liquidity_snapshots(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        rows=[
            {
                "pool_id": pool_id,
                "has_liquidity": False,
                "liquidity": "",
                "depth_status": "no_liquidity",
                "liquidity_block_number": 134_757_678,
                "evidence_source": "pool_manager_extsload_live_state_batch",
            }
        ],
    )
    row = load_v4_pool_liquidity(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
    )[0]
    assert row["has_liquidity"] == 0


def test_import_prefers_index_extsload_over_stale_swap_liquidity_file(tmp_path):
    root = tmp_path / "runtime"
    database = str(tmp_path / "local.sqlite3")
    ensure_database_schema(database)
    pool_id = "0x" + "ee" * 32
    index = {
        "schemaVersion": "robinhood-v4-pool-index-v1",
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "poolManager": ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        "generatedBlockNumber": 134_757_678,
        "initializeLogCount": 1,
        "poolKeys": [],
        "pools": [
            _pool(
                pool_id,
                hasLiquidity=True,
                liquidity=str(10**18),
                liquidityIndex=1.0,
                estimatedTvlUsd="6000",
                depthStatus="deep",
                liquidityReadAt="2026-10-10T00:00:00Z",
                liquidityBlockNumber=134_757_678,
            )
        ],
    }
    liquidity = {
        "schemaVersion": "robinhood-testnet-v4-pool-liquidity-v1",
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "generatedAt": "2026-10-10T00:00:00Z",
        "pools": [
            {
                "poolId": pool_id,
                "hasLiquidity": False,
                "liquidity": "",
                "liquidityIndex": 0,
                "estimatedTvlUsd": 0,
                "depthStatus": "no_liquidity",
                "recentSwapActive": True,
            }
        ],
    }
    _write_json(root / "evidence" / ROBINHOOD_TESTNET_NETWORK / "v4_pool_index" / "latest.json", index)
    _write_json(root / "evidence" / ROBINHOOD_TESTNET_NETWORK / "v4_pool_liquidity" / "latest.json", liquidity)

    result = import_existing_json(database, runtime_root=root, network=ROBINHOOD_TESTNET_NETWORK)
    assert result["ok"] is True
    assert result["v4PoolCount"] == 1

    row = load_v4_pool_liquidity(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
    )[0]
    assert row["pool_id"] == pool_id
    assert row["has_liquidity"] == 1
    assert row["liquidity"] == str(10**18)
    # The index row does not carry recentSwapActive, so the liquidity file's
    # explicit true must not be overwritten by an implicit false default.
    assert row["recent_swap_active"] == 1


def test_import_keeps_extsload_missing_unknown_not_false_negative(tmp_path):
    root = tmp_path / "runtime"
    database = str(tmp_path / "local.sqlite3")
    ensure_database_schema(database)
    unknown_id = "0x" + "11" * 32
    zero_id = "0x" + "22" * 32
    index = {
        "schemaVersion": "robinhood-v4-pool-index-v1",
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "poolManager": ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        "generatedBlockNumber": 134_757_678,
        "initializeLogCount": 2,
        "poolKeys": [],
        "pools": [
            _pool(
                unknown_id,
                hasLiquidity=False,
                liquidityError="pool manager live state batch missing slot0/liquidity",
            ),
            _pool(
                zero_id,
                hasLiquidity=False,
                liquidityError="robinhood testnet pool manager live liquidity is zero",
            ),
        ],
    }
    _write_json(root / "evidence" / ROBINHOOD_TESTNET_NETWORK / "v4_pool_index" / "latest.json", index)
    _write_json(
        root / "evidence" / ROBINHOOD_TESTNET_NETWORK / "v4_pool_liquidity" / "latest.json",
        {"pools": []},
    )

    import_existing_json(database, runtime_root=root, network=ROBINHOOD_TESTNET_NETWORK)
    rows = {
        row["pool_id"]: row
        for row in load_v4_pool_liquidity(
            database,
            network=ROBINHOOD_TESTNET_NETWORK,
            chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
            protocol="uniswap_v4",
        )
    }
    assert rows[unknown_id]["has_liquidity"] is None
    assert rows[zero_id]["has_liquidity"] == 0


def test_mark_testnet_pool_index_uses_live_extsload_by_default(tmp_path, monkeypatch):
    pool_id = "0x" + "33" * 32
    index = {
        "schemaVersion": "robinhood-v4-pool-index-v1",
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "poolManager": ROBINHOOD_TESTNET_V4_POOL_MANAGER,
        "generatedBlockNumber": 134_757_678,
        "initializeLogCount": 1,
        "poolKeys": [],
        "pools": [_pool(pool_id)],
    }
    index_path = tmp_path / "v4_pool_index" / "latest.json"
    _write_json(index_path, index)

    class _FakeEth:
        chain_id = ROBINHOOD_TESTNET_CHAIN_ID

    class _FakeW3:
        eth = _FakeEth()

    def fake_connect(network, chain_id):
        assert network == ROBINHOOD_TESTNET_NETWORK
        assert chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        return _FakeW3(), "https://fake-testnet.example"

    def fake_batch(**kwargs):
        return {
            pool_id: {
                "sqrtPriceX96": str(2**96),
                "tick": 0,
                "liquidity": str(10**18),
                "blockNumber": 134_757_678,
                "source": "pool_manager_extsload_live_state_batch",
            }
        }

    monkeypatch.setattr(testnet_liquidity, "_connect_chain_rpc", fake_connect)
    monkeypatch.setattr(testnet_liquidity, "_batch_read_live_pool_states", fake_batch)
    result = testnet_liquidity.mark_pool_liquidity_for_pool_index(
        output_path=index_path,
        pool_ids=[pool_id],
    )

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    row = next(item for item in payload["pools"] if item["poolId"] == pool_id)
    assert row["hasLiquidity"] is True
    assert row["liquidity"] == str(10**18)
    assert row["liquiditySource"] == "pool_manager_extsload_live_state_batch"
    assert result["liquidityMarkerActive"] is True
    assert result["liquidityMarkedPoolCount"] == 1
    assert result["liquidityRequestedPoolIds"] == [pool_id]


def test_testnet_liquidity_builder_enables_live_mark_by_default(tmp_path, monkeypatch):
    pool_id = "0x" + "44" * 32
    manager = ROBINHOOD_TESTNET_V4_POOL_MANAGER
    metadata_path = tmp_path / "contracts.json"
    report_path = tmp_path / "preflight" / "report.json"
    _write_json(
        metadata_path,
        {
            "defaultV4PoolManager": manager,
            "lastReadinessEvidence": str(report_path),
        },
    )
    report = {
        "runAt": "2026-10-10T00:00:00Z",
        "rpc": {"chainId": ROBINHOOD_TESTNET_CHAIN_ID, "blockNumber": 134_757_678},
        "flashAccountingLiquidity": {
            "address": manager,
            "activePoolIds": [pool_id],
            "fromBlock": 1,
            "toBlock": 134_757_678,
        },
    }
    _write_json(report_path, report)
    decoded_row = _pool(pool_id)

    def fake_load(seed_path, *, allow_fallback):
        return seed_path, report

    def fake_enrich(pool_ids, *, pool_manager, rpc_url, swap_from_block, to_block):
        assert pool_manager == manager
        return [decoded_row], [decoded_row], []

    captured: dict = {}

    def fake_apply(pools, *, w3, rpc_url, pool_manager, batch_size):
        captured["selected"] = list(pools)
        captured["rpc_url"] = rpc_url
        for pool in pools:
            pool["hasLiquidity"] = True
            pool["liquidity"] = str(10**18)
        return {
            "liquidityMarkerActive": True,
            "liquidityMarkedPoolCount": 1,
            "liquidityErrorCount": 0,
            "liquidityErrors": [],
            "liquidityScanAt": "2026-10-10T00:00:00Z",
            "liquidityPoolIds": [pool_id],
            "liquiditySource": "pool_manager_extsload_live_state_batch",
        }

    monkeypatch.setattr(testnet_liquidity, "_load_best_preflight_report", fake_load)
    monkeypatch.setattr(testnet_liquidity, "_enrich_pool_ids_with_rpc", fake_enrich)
    monkeypatch.setattr(testnet_liquidity, "_apply_extsload_liquidity", fake_apply)

    result = testnet_liquidity.build_v4_pool_liquidity_from_contract_evidence(
        contract_metadata_path=metadata_path,
        preflight_report_path=report_path,
        decode_pool_keys=True,
        token_balance_scan_scope="none",
        rpc_url="https://fake-testnet.example",
        pool_limit=10,
        liquidity_batch_size=2,
    )

    assert result["liquidityMarkerActive"] is True
    assert result["liquidityMarkedPoolCount"] == 1
    assert result["liquiditySource"] == "pool_manager_extsload_live_state_batch"
    assert captured["selected"][0]["fee"] == 3000
    assert captured["selected"][0]["hooks"] == ZERO_ADDRESS


def test_mainnet_enrich_pool_liquidity_preserves_unknown_on_read_failure(monkeypatch):
    pool_id = "0x" + "55" * 32
    pools = [{"poolId": pool_id, "hasLiquidity": True}]

    def fail_read(**kwargs):
        raise ValueError("RPC timeout")

    monkeypatch.setattr(mainnet_pool_index, "_read_live_pool_state_with_w3", fail_read)
    activity = mainnet_pool_index._enrich_pool_liquidity(
        object(),
        pools,
        pool_manager="0x" + "66" * 20,
        rpc_url=None,
    )
    assert pools[0]["hasLiquidity"] is True
    assert pools[0]["depthStatus"] == "liquidity_read_failed"
    assert activity["liquidityMarkedPoolCount"] == 0
    assert activity["liquidityPoolIds"] == []


def test_mainnet_enrich_pool_liquidity_flips_only_on_explicit_zero(monkeypatch):
    pool_id = "0x" + "77" * 32
    pools = [{"poolId": pool_id, "hasLiquidity": True}]

    def zero_read(**kwargs):
        raise ValueError("robinhood pool manager live liquidity is zero")

    monkeypatch.setattr(mainnet_pool_index, "_read_live_pool_state_with_w3", zero_read)
    activity = mainnet_pool_index._enrich_pool_liquidity(
        object(),
        pools,
        pool_manager="0x" + "66" * 20,
        rpc_url=None,
    )
    assert pools[0]["hasLiquidity"] is False
    assert pools[0]["depthStatus"] == "no_liquidity"
    assert activity["liquidityMarkedPoolCount"] == 0
    assert activity["liquidityPoolIds"] == []
