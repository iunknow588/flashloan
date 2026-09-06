import json

import pytest

from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.intent import build_robinhood_intent_draft, normalize_network


def _pool(pair: str, pool: str) -> dict:
    left, right = pair.split(":")
    addresses = {
        "WETH": robinhood.WETH_ADDRESS,
        "USDC": "0x00000000000000000000000000000000000000a1",
        "TOKEN": "0x00000000000000000000000000000000000000b2",
    }
    return {
        "network": "robinhood",
        "chainId": 4663,
        "dex": "verified_test_dex",
        "protocol": "v3",
        "factory": "0x00000000000000000000000000000000000000f1",
        "router": "0x00000000000000000000000000000000000000f2",
        "quoter": "0x00000000000000000000000000000000000000f3",
        "pool": pool,
        "fee": "500",
        "symbolPair": pair,
        "token0Symbol": left,
        "token0Address": addresses[left],
        "token1Symbol": right,
        "token1Address": addresses[right],
        "status": "recommended",
        "tvlUsd": 250000.0,
    }


def _table() -> dict:
    return {
        "network": "robinhood",
        "chainId": 4663,
        "generatedAt": "2026-08-17T00:00:00Z",
        "scannedDexes": ["verified_test_dex"],
        "pools": [
            _pool("WETH:USDC", "0x0000000000000000000000000000000000000001"),
            _pool("USDC:TOKEN", "0x0000000000000000000000000000000000000002"),
            _pool("TOKEN:WETH", "0x0000000000000000000000000000000000000003"),
        ],
    }


def test_robinhood_network_aliases_are_normalized():
    assert normalize_network("robinhood-chain") == "robinhood"
    assert normalize_network("4663") == "robinhood"
    assert normalize_network("robinhood-testnet") == "robinhood-testnet"
    assert normalize_network("46630") == "robinhood-testnet"


def test_robinhood_config_uses_official_chain_level_addresses_only():
    assert robinhood.CHAIN_ID == 4663
    assert robinhood.TESTNET_CHAIN_ID == 46630
    assert robinhood.NATIVE_SYMBOL == "ETH"
    assert robinhood.TOKENS["WETH"].address == "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
    assert robinhood.TESTNET_TOKENS["WETH"].address == "0x7943e237c7F95DA44E0301572D358911207852Fa"
    assert robinhood.TESTNET_MULTICALL_ADDRESS == "0xa432504b6F04Cafe775b09D8AA92e8dbe41Ec7a8"
    assert robinhood.DEXES == ()


def test_robinhood_intent_draft_is_shadow_only_and_table_driven(tmp_path):
    cache_path = tmp_path / "robinhood_pool_table.json"
    cache_path.write_text(json.dumps(_table()), encoding="utf-8")

    draft = build_robinhood_intent_draft(
        ("WETH", "USDC", "TOKEN", "WETH"),
        amount="1",
        static_table_path=cache_path,
    )

    assert draft["network"] == "robinhood"
    assert draft["chainId"] == 4663
    assert draft["mode"] == "shadow_quote_only"
    assert draft["executionEnabled"] is False
    assert draft["broadcastEnabled"] is False
    assert draft["borrowToken"]["address"] == robinhood.WETH_ADDRESS
    assert [hop["protocol"] for hop in draft["hops"]] == ["v3", "v3", "v3"]
    assert draft["gasPolicy"]["chainWideFreeGas"] is False


def test_robinhood_intent_rejects_non_robinhood_static_table(tmp_path):
    cache_path = tmp_path / "bnb_pool_table.json"
    cache_path.write_text(json.dumps({"network": "bnb", "chainId": 56, "pools": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="robinhood chain static pool table"):
        build_robinhood_intent_draft(
            ("WETH", "USDC", "TOKEN", "WETH"),
            static_table_path=cache_path,
        )


def test_robinhood_intent_rejects_pool_missing_token_addresses(tmp_path):
    table = _table()
    table["pools"][0].pop("token1Address")
    cache_path = tmp_path / "robinhood_pool_table.json"
    cache_path.write_text(json.dumps(table), encoding="utf-8")

    with pytest.raises(ValueError, match="missing token addresses"):
        build_robinhood_intent_draft(
            ("WETH", "USDC", "TOKEN", "WETH"),
            static_table_path=cache_path,
        )
