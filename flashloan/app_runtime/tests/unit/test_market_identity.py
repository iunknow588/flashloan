import pytest

from core.market_identity import (
    MarketInfo,
    MarketKey,
    canonical_market_id,
    canonical_market_id_from_legacy,
    market_info_from_summary,
    market_key_from_summary,
    market_registry_from_summaries,
    normalize_feature,
    normalize_protocol_id,
    validate_market_identity_registry,
)


def test_normalizes_feature_aliases():
    assert normalize_feature("BOT") == "liquidation"
    assert normalize_feature("bot") == "liquidation"
    assert normalize_feature("liquidation") == "liquidation"
    assert normalize_feature("DEX") == "dex_arbitrage"
    assert normalize_feature("dex") == "dex_arbitrage"
    assert normalize_feature("dex_arbitrage") == "dex_arbitrage"

    with pytest.raises(ValueError, match="unknown feature"):
        normalize_feature("risk")


def test_normalizes_protocol_id_to_snake_case():
    assert normalize_protocol_id("Venus Core") == "venus_core"
    assert normalize_protocol_id("pancakeswap-v3") == "pancakeswap_v3"


def test_builds_canonical_market_id():
    assert canonical_market_id("liquidation", 56, "venus_core") == "liquidation-56-venus-core"
    assert canonical_market_id("dex_arbitrage", 56, "pancakeswap_v3") == "dex-arbitrage-56-pancakeswap-v3"
    assert (
        canonical_market_id("dex_arbitrage", None, "hyperliquid_native", network="hyperliquid")
        == "dex-arbitrage-hyperliquid-hyperliquid-native"
    )


def test_market_info_preserves_canonical_and_legacy_ids():
    info = market_info_from_summary(
        {
            "market_id": "bnb-venus-core",
            "feature": "liquidation",
            "chain_id": 56,
            "network": "bnb",
            "protocol": "venus_core",
            "display_name": "BNB Venus Core",
            "market_type": "native_chain",
        }
    )

    assert info.key == MarketKey(
        feature="liquidation",
        chain_id=56,
        protocol_id="venus_core",
        market_id="liquidation-56-venus-core",
        legacy_market_id="bnb-venus-core",
    )
    assert info.market_id == "liquidation-56-venus-core"
    assert info.legacy_market_id == "bnb-venus-core"
    assert "bnb-venus-core" in info.aliases


def test_registry_indexes_key_canonical_id_and_aliases():
    registry = market_registry_from_summaries(
        [
            {
                "market_id": "bnb-venus-core",
                "feature": "liquidation",
                "chain_id": 56,
                "network": "bnb",
                "protocol": "venus_core",
            },
            {
                "market_id": "bnb-dex-arbitrage",
                "feature": "dex_arbitrage",
                "chain_id": 56,
                "network": "bnb",
                "protocol": "pancakeswap_v3",
            },
        ]
    )

    liquidation_key = registry.key_for_market_id("bnb-venus-core")
    dex_key = registry.key_for_market_id("dex-arbitrage-56-pancakeswap-v3")

    assert liquidation_key is not None
    assert dex_key is not None
    assert registry.by_key[liquidation_key].market_id == "liquidation-56-venus-core"
    assert registry.by_key[dex_key].legacy_market_id == "bnb-dex-arbitrage"


def test_builtin_aliases_resolve_to_canonical_ids():
    assert canonical_market_id_from_legacy("bnb-venus-core") == "liquidation-56-venus-core"
    assert canonical_market_id_from_legacy("bnb-dex-arbitrage") == "dex-arbitrage-56-pancakeswap-v3"
    assert (
        canonical_market_id_from_legacy("hyperliquid-native")
        == "dex-arbitrage-hyperliquid-hyperliquid-native"
    )


def test_registry_rejects_duplicate_triplets():
    with pytest.raises(ValueError, match="duplicate"):
        validate_market_identity_registry(
            [
                {
                    "market_id": "first",
                    "feature": "liquidation",
                    "chain_id": 56,
                    "network": "bnb",
                    "protocol": "venus_core",
                },
                {
                    "market_id": "second",
                    "feature": "liquidation",
                    "chain_id": 56,
                    "network": "bnb",
                    "protocol": "venus_core",
                },
            ]
        )
