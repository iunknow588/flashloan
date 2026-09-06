import json

import pytest

from dex.intent_trade.chains.bnb import config as bnb
from dex.intent_trade.chains.bnb.intent import build_bnb_intent_draft, normalize_network


def _pool(pair: str, pool: str) -> dict:
    left, right = pair.split(":")
    return {
        "network": "bnb",
        "chainId": 56,
        "dex": "pancakeswap_v2",
        "protocol": "v2",
        "router": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
        "pool": pool,
        "fee": "25 bps",
        "symbolPair": pair,
        "token0Symbol": left,
        "token1Symbol": right,
        "status": "recommended",
        "tvlUsd": 250000.0,
    }


def _table() -> dict:
    return {
        "network": "bnb",
        "chainId": 56,
        "generatedAt": "2026-08-15T00:00:00Z",
        "scannedDexes": ["pancakeswap_v2"],
        "pools": [
            _pool("BTCB:USDT", "0x0000000000000000000000000000000000000001"),
            _pool("BTCB:WBNB", "0x0000000000000000000000000000000000000002"),
            _pool("USDT:WBNB", "0x0000000000000000000000000000000000000003"),
            {
                **_pool("BTCB:USDT", "0x0000000000000000000000000000000000000004"),
                "protocol": "v3",
                "dex": "pancakeswap_v3",
                "tvlUsd": 999999.0,
            },
        ],
    }


def test_bnb_network_aliases_are_normalized():
    assert normalize_network("bnb") == "bnb"
    assert normalize_network("bsc") == "bnb"
    assert normalize_network("56") == "bnb"
    assert normalize_network("avalanche") == "avalanche"


def test_bnb_triangle_routes_only_use_complete_edges():
    pools = _table()["pools"][:3]
    routes = bnb.triangle_routes(bnb.best_edges_by_pair(pools, {"recommended"}))

    assert [item["path"] for item in routes] == ["BTCB -> USDT -> WBNB -> BTCB"]
    assert routes[0]["status"] == "recommended"


def test_bnb_intent_draft_uses_only_bnb_v2_pool_table(tmp_path):
    cache_path = tmp_path / "bnb_pool_table.json"
    cache_path.write_text(json.dumps(_table()), encoding="utf-8")

    draft = build_bnb_intent_draft(
        ("USDT", "BTCB", "WBNB", "USDT"),
        amount="1000",
        static_table_path=cache_path,
        allowed_protocols={"v2"},
    )

    assert draft["network"] == "bnb"
    assert draft["chainId"] == 56
    assert draft["mode"] == "shadow_quote_only"
    assert draft["executionEnabled"] is False
    assert [hop["protocol"] for hop in draft["hops"]] == ["v2", "v2", "v2"]
    assert draft["hops"][0]["tokenInAddress"].lower() == bnb.TOKENS["USDT"].address.lower()


def test_bnb_intent_draft_can_select_a_higher_tvl_v3_pool(tmp_path):
    cache_path = tmp_path / "bnb_pool_table.json"
    cache_path.write_text(json.dumps(_table()), encoding="utf-8")

    draft = build_bnb_intent_draft(
        ("USDT", "BTCB", "WBNB", "USDT"),
        static_table_path=cache_path,
    )

    assert draft["hops"][0]["protocol"] == "v3"
    assert draft["hops"][0]["pool"] == "0x0000000000000000000000000000000000000004"


def test_bnb_intent_rejects_non_bnb_static_table(tmp_path):
    cache_path = tmp_path / "avalanche_pool_table.json"
    cache_path.write_text(json.dumps({"network": "avalanche", "chainId": 43114, "pools": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="bnb chain static pool table"):
        build_bnb_intent_draft(
            ("USDT", "BTCB", "WBNB", "USDT"),
            static_table_path=cache_path,
        )
