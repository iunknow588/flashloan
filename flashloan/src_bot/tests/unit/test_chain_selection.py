import pytest

from core.chain_selection import (
    ChainSelectionError,
    available_chain_selections,
    resolve_chain_selection,
)


def _rows():
    return [
        {
            "market_id": "avalanche-aave-v3",
            "chain_id": 43114,
            "feature": "liquidation",
            "network": "avalanche",
            "protocol": "aave_v3",
            "display_name": "Avalanche Aave V3",
            "native_symbol": "AVAX",
            "protocol_supported": True,
            "observation_only": False,
            "is_default": True,
        },
        {
            "market_id": "bnb-aave-v3",
            "chain_id": 56,
            "feature": "liquidation",
            "network": "bnb",
            "protocol": "aave_v3",
            "display_name": "BNB Aave V3",
            "native_symbol": "BNB",
            "protocol_supported": True,
            "observation_only": False,
        },
        {
            "market_id": "bnb-venus-core",
            "chain_id": 56,
            "feature": "liquidation",
            "network": "bnb",
            "protocol": "venus_core",
            "display_name": "BNB Venus Core",
            "native_symbol": "BNB",
            "protocol_supported": False,
            "observation_only": True,
        },
        {
            "market_id": "bnb-dex-arbitrage",
            "chain_id": 56,
            "feature": "dex_arbitrage",
            "network": "bnb",
            "protocol": "pancakeswap_v3",
            "display_name": "BNB PancakeSwap V3 Arbitrage",
            "native_symbol": "BNB",
            "protocol_supported": False,
            "observation_only": True,
            "market_type": "native_chain",
        },
        {
            "market_id": "bnb-pancakeswap-v2",
            "chain_id": 56,
            "feature": "dex_arbitrage",
            "network": "bnb",
            "protocol": "pancakeswap_v2",
            "display_name": "BNB PancakeSwap V2 Arbitrage",
            "native_symbol": "BNB",
            "protocol_supported": False,
            "observation_only": True,
            "market_type": "native_chain",
        },
        {
            "market_id": "cow-dex-arbitrage",
            "chain_id": 0,
            "feature": "dex_arbitrage",
            "network": "cow",
            "protocol": "cow_protocol",
            "display_name": "COW Cross-Chain Execution",
            "native_symbol": "COW",
            "protocol_supported": False,
            "observation_only": True,
            "market_type": "execution_rail",
            "is_default": True,
        },
        {
            "market_id": "robinhood-dex-shadow",
            "chain_id": 4663,
            "feature": "dex_arbitrage",
            "network": "robinhood",
            "protocol": "uniswap_v3",
            "display_name": "Robinhood DEX Shadow",
            "native_symbol": "ETH",
            "protocol_supported": False,
            "observation_only": True,
            "market_type": "shadow_chain",
        },
        {
            "market_id": "robinhood-liquidation-shadow",
            "chain_id": 4663,
            "feature": "liquidation",
            "network": "robinhood",
            "protocol": "aave_v3",
            "display_name": "Robinhood Liquidation Shadow",
            "native_symbol": "ETH",
            "protocol_supported": False,
            "observation_only": True,
            "market_type": "shadow_chain",
        },
    ]


def test_selection_keeps_same_chain_markets_distinct(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    assert resolve_chain_selection(chain_id=56, market_id="bnb-venus-core").market_id == "bnb-venus-core"
    assert resolve_chain_selection(chain_id=56, market_id="bnb-aave-v3").protocol == "aave_v3"


def test_selection_filters_by_feature_and_default_market(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    choices = available_chain_selections(feature="dex_arbitrage")
    assert [choice.market_id for choice in choices] == [
        "bnb-dex-arbitrage",
        "bnb-pancakeswap-v2",
        "cow-dex-arbitrage",
        "robinhood-dex-shadow",
    ]
    selected = resolve_chain_selection(feature="dex_arbitrage")
    assert selected.market_id == "cow-dex-arbitrage"
    assert selected.chain_id == 0
    assert selected.market_type == "execution_rail"
    assert selected.capabilities["robinhood_shadow"] is False
    assert selected.capabilities["execution"] is False


def test_selection_exposes_robinhood_in_liquidation_scope(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    choices = available_chain_selections(feature="liquidation")
    market_ids = [choice.market_id for choice in choices]
    robinhood = resolve_chain_selection(chain_id=4663, feature="liquidation")

    assert "robinhood-liquidation-shadow" in market_ids
    assert robinhood.market_id == "robinhood-liquidation-shadow"
    assert robinhood.capabilities["robinhood_shadow"] is True
    assert robinhood.capabilities["execution"] is False


def test_selection_rejects_chain_mismatch(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    with pytest.raises(ChainSelectionError, match="belongs to chain_id"):
        resolve_chain_selection(market_id="bnb-aave-v3", chain_id=43114)


def test_selection_rejects_unknown_market(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    with pytest.raises(ChainSelectionError, match="unknown market_id"):
        resolve_chain_selection(market_id="missing-market")


def test_selection_reports_feature_mismatch_for_known_market_in_other_workflow(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    with pytest.raises(ChainSelectionError) as exc_info:
        resolve_chain_selection(market_id="avalanche-aave-v3", chain_id=43114, feature="dex_arbitrage")

    assert exc_info.value.code == "feature_mismatch"
    assert "belongs to feature liquidation" in exc_info.value.message
