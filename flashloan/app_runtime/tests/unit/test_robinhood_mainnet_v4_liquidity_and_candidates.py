"""Mainnet liquidity evidence writer and all-pool static candidate tests."""

from __future__ import annotations

import json
from pathlib import Path

from dex.intent_trade.chains.robinhood import v4_pool_liquidity
from dex.intent_trade.chains.robinhood.dex_pool_candidates import (
    build_mainnet_static_candidate_payload,
)
from dex.intent_trade.chains.robinhood import v4_quote_adapter
from dex.intent_trade.chains.robinhood.v4_pool_index import ZERO_ADDRESS


def _index_payload() -> dict:
    return {
        "schemaVersion": "robinhood-v4-pool-index-v1",
        "network": "robinhood",
        "chainId": 4663,
        "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
        "generatedBlockNumber": 84_000_000,
        "poolKeys": [],
        "pools": [],
    }


def test_liquidity_writer_outputs_required_evidence_fields(tmp_path, monkeypatch):
    index = _index_payload()
    index["pools"] = [
        {
            "protocol": "uniswap_v4",
            "poolId": "0x" + "aa" * 32,
            "poolManager": index["poolManager"],
            "token0": ZERO_ADDRESS,
            "token1": "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",
            "token0Symbol": "ETH",
            "token1Symbol": "USDE",
            "token0Decimals": 18,
            "token1Decimals": 18,
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": ZERO_ADDRESS,
            "sqrtPriceX96": str(2**96),
            "tick": 10,
        },
        {
            "protocol": "uniswap_v4",
            "poolId": "0x" + "bb" * 32,
            "poolManager": index["poolManager"],
            "token0": ZERO_ADDRESS,
            "token1": "0x" + "11" * 20,
            "token0Symbol": "ETH",
            "token1Symbol": "X",
            "token0Decimals": 18,
            "token1Decimals": 18,
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": ZERO_ADDRESS,
            "sqrtPriceX96": str(2**96),
            "tick": 10,
        },
    ]
    index_path = tmp_path / "v4_pool_index" / "latest.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(json.dumps(index), encoding="utf-8")

    class _FakeEth:
        chain_id = 4663
        block_number = 84_000_100

    class _FakeWeb3:
        eth = _FakeEth()

    def fake_connect(url):
        assert isinstance(url, str)
        return _FakeWeb3()

    def fake_connect_chain(network, chain_id):
        assert network == "robinhood"
        assert chain_id == 4663
        return _FakeWeb3(), "https://fake-mainnet.example"

    def fake_enrich(w3, pools, *, pool_manager, rpc_url=None, pool_ids=None):
        for pool in pools:
            if pool.get("poolId") == "0x" + "aa" * 32:
                pool["hasLiquidity"] = True
                pool["liquidityIndex"] = 123.5
                pool["estimatedTvlUsd"] = "260000.5"
                pool["depthStatus"] = "deep"
                pool["liquidityReadAt"] = "2026-10-10T00:00:00Z"
                pool["liquidityBlockNumber"] = 84_000_100
                pool["balances"] = {"tvlUsd": "260000.5"}
            else:
                pool["hasLiquidity"] = False
                pool["liquidityIndex"] = 0
                pool["estimatedTvlUsd"] = 0
                pool["depthStatus"] = "no_liquidity"
                pool["liquidityReadAt"] = "2026-10-10T00:00:00Z"
                pool["balances"] = {"tvlUsd": 0}
        return {
            "liquidityMarkerActive": True,
            "liquidityMarkedPoolCount": 1,
            "liquidityErrorCount": 0,
            "liquidityErrors": [],
            "liquidityScanAt": "2026-10-10T00:00:00Z",
            "liquidityPoolIds": ["0x" + "aa" * 32],
        }

    monkeypatch.setattr(v4_pool_liquidity, "_connect_rpc", fake_connect)
    monkeypatch.setattr(v4_pool_liquidity, "_connect_chain_rpc", fake_connect_chain)
    with v4_quote_adapter._RPC_CONNECTION_CACHE_LOCK:
        v4_quote_adapter._RPC_CONNECTION_CACHE.clear()
    monkeypatch.setattr(v4_pool_liquidity, "_enrich_pool_liquidity", fake_enrich)

    output_path = tmp_path / "v4_pool_liquidity" / "latest.json"
    result = v4_pool_liquidity.write_v4_pool_liquidity_from_contract_evidence(
        path=output_path,
        index_path=index_path,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["poolCount"] == 2
    assert payload["generatedBlockNumber"] == 84_000_100
    assert payload["liquidityMarkedPoolCount"] == 1
    liquid = next(row for row in payload["pools"] if row["poolId"] == "0x" + "aa" * 32)
    for field in (
        "hasLiquidity",
        "liquidityIndex",
        "estimatedTvlUsd",
        "depthStatus",
        "liquidityReadAt",
        "balances",
    ):
        assert field in liquid
    assert liquid["hasLiquidity"] is True
    assert liquid["depthStatus"] == "deep"


def test_static_candidate_payload_keeps_every_indexed_pool_when_max_per_pair_zero(
    monkeypatch,
):
    usde = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
    rows = [
        {
            "protocol": "uniswap_v4",
            "poolId": "0x" + "cc" * 32,
            "token0": ZERO_ADDRESS,
            "token1": usde,
            "token0Symbol": "ETH",
            "token1Symbol": "USDE",
            "token0Decimals": 18,
            "token1Decimals": 18,
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": ZERO_ADDRESS,
            "sqrtPriceX96": str(2**96),
            "tick": 10,
            "hasLiquidity": True,
            "liquidityIndex": 100,
            "estimatedTvlUsd": "1000000",
        },
        {
            "protocol": "uniswap_v4",
            "poolId": "0x" + "dd" * 32,
            "token0": ZERO_ADDRESS,
            "token1": usde,
            "token0Symbol": "ETH",
            "token1Symbol": "USDE",
            "token0Decimals": 18,
            "token1Decimals": 18,
            "fee": 8388608,
            "tickSpacing": 60,
            "hooks": ZERO_ADDRESS,
            "sqrtPriceX96": str(2**96),
            "tick": 10,
            "hasLiquidity": True,
            "liquidityIndex": 99,
            "estimatedTvlUsd": "500000",
        },
        {
            "protocol": "uniswap_v4",
            "poolId": "0x" + "ee" * 32,
            "token0": ZERO_ADDRESS,
            "token1": usde,
            "token0Symbol": "ETH",
            "token1Symbol": "USDE",
            "token0Decimals": 18,
            "token1Decimals": 18,
            "fee": 1000,
            "tickSpacing": 60,
            "hooks": ZERO_ADDRESS,
            "sqrtPriceX96": str(2**96),
            "tick": 10,
            "hasLiquidity": False,
            "liquidityIndex": 0,
        },
    ]
    index = _index_payload()
    index["pools"] = rows
    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood.dex_pool_candidates._decorate_candidates_with_live_state",
        lambda candidates, *, pool_manager, rpc_url: {
            "liveStateReadAttempted": False,
            "liveStatePoolCount": 0,
            "liveLiquidPoolCount": 0,
            "liveNoLiquidityPoolCount": 0,
            "error": "unit_test_mock",
        },
    )

    payload = build_mainnet_static_candidate_payload(
        index,
        rows,
        max_candidates_per_pair=0,
        rpc_url="",
    )

    assert len(payload["pools"]) == 3
    assert payload["summary"]["poolCount"] == 3
    assert payload["summary"]["sourceIndexedPoolCount"] == 3
    assert payload["summary"]["specialFeePoolCount"] == 1
