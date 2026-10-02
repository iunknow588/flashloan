import pytest
from flask import Flask

from core.chain_selection import (
    ChainSelectionError,
    available_chain_selections,
    resolve_chain_selection,
)
from web.chain_selection import chain_selection_payload


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
            "market_id": "avalanche-dex-arbitrage",
            "chain_id": 43114,
            "feature": "dex_arbitrage",
            "network": "avalanche",
            "protocol": "uniswap_v3",
            "display_name": "Avalanche DEX Arbitrage",
            "native_symbol": "AVAX",
            "protocol_supported": False,
            "observation_only": True,
            "market_type": "native_chain",
            "is_default": True,
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
            "market_id": "robinhood-testnet-dex-paused",
            "chain_id": 46630,
            "feature": "dex_arbitrage",
            "network": "robinhood-testnet",
            "protocol": "uniswap_v4",
            "display_name": "Robinhood Testnet DEX Arbitrage",
            "native_symbol": "ETH",
            "protocol_supported": True,
            "observation_only": False,
            "market_type": "native_chain",
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
        {
            "market_id": "robinhood-testnet-liquidation-shadow",
            "chain_id": 46630,
            "feature": "liquidation",
            "network": "robinhood-testnet",
            "protocol": "morpho_blue",
            "display_name": "Robinhood Testnet Liquidation Shadow",
            "native_symbol": "ETH",
            "protocol_supported": False,
            "observation_only": True,
            "market_type": "native_chain",
        },
    ]


def test_selection_keeps_same_chain_markets_distinct(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    venus = resolve_chain_selection(chain_id=56, market_id="bnb-venus-core")
    aave = resolve_chain_selection(chain_id=56, market_id="bnb-aave-v3")

    assert venus.market_id == "liquidation-56-venus-core"
    assert venus.legacy_market_id == "bnb-venus-core"
    assert aave.market_id == "liquidation-56-aave-v3"
    assert aave.protocol == "aave_v3"


def test_selection_filters_by_feature_and_default_market(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    choices = available_chain_selections(feature="dex_arbitrage")
    assert [choice.market_id for choice in choices] == [
        "dex-arbitrage-43114-uniswap-v3",
        "dex-arbitrage-56-pancakeswap-v3",
        "dex-arbitrage-56-pancakeswap-v2",
        "dex-arbitrage-0-cow-protocol",
        "dex-arbitrage-4663-uniswap-v3",
        "dex-arbitrage-46630-uniswap-v4",
    ]
    selected = resolve_chain_selection(feature="dex_arbitrage")
    assert selected.market_id == "dex-arbitrage-46630-uniswap-v4"
    assert selected.legacy_market_id == "robinhood-testnet-dex-paused"
    assert selected.chain_id == 46630
    assert selected.network == "robinhood-testnet"
    assert selected.market_type == "native_chain"
    assert selected.capabilities["robinhood_shadow"] is False
    assert selected.capabilities["execution"] is True


def test_selection_without_feature_defaults_to_avalanche_aave(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    selected = resolve_chain_selection()

    assert selected.market_id == "liquidation-43114-aave-v3"
    assert selected.legacy_market_id == "avalanche-aave-v3"
    assert selected.chain_id == 43114
    assert selected.global_chain_id == "chain:43114"
    assert selected.as_dict()["chain_key"] == "chain:43114"
    assert selected.network == "avalanche"
    assert selected.protocol == "aave_v3"


def test_dex_selection_defaults_to_robinhood_testnet_without_page_context(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    selected = resolve_chain_selection(feature="dex_arbitrage")

    assert selected.market_id == "dex-arbitrage-46630-uniswap-v4"
    assert selected.legacy_market_id == "robinhood-testnet-dex-paused"
    assert selected.chain_id == 46630
    assert selected.global_chain_id == "chain:46630"
    assert selected.network == "robinhood-testnet"
    assert selected.protocol == "uniswap_v4"


def test_flash_loan_selector_payload_defaults_to_robinhood_testnet(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    with app.test_request_context("/api/chain-selection?page_path=/flash-loan/pools"):
        payload = chain_selection_payload()

    assert payload["selected"]["market_id"] == "dex-arbitrage-46630-uniswap-v4"
    assert payload["selected"]["legacy_market_id"] == "robinhood-testnet-dex-paused"
    assert payload["selected"]["chain_id"] == 46630
    assert payload["selected"]["global_chain_id"] == "chain:46630"
    assert payload["selected"]["chain_config"]["network"] == "robinhood-testnet"
    assert payload["selected_chain_config"]["flash_loan"]["candidate_protocols"] == ["uniswap_v4"]
    assert payload["market_scope"]["global_chain_id"] == "chain:46630"
    assert payload["selected"]["protocol"] == "uniswap_v4"


def test_bot_selector_payload_defaults_to_robinhood_testnet(monkeypatch):
    app = Flask(__name__)
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    with app.test_request_context("/api/chain-selection?page_path=/bot"):
        payload = chain_selection_payload()

    assert payload["selected"]["market_id"] == "liquidation-46630-morpho-blue"
    assert payload["selected"]["legacy_market_id"] == "robinhood-testnet-liquidation-shadow"
    assert payload["selected"]["chain_id"] == 46630
    assert payload["selected"]["global_chain_id"] == "chain:46630"
    assert payload["selected"]["chain_config"]["network"] == "robinhood-testnet"
    assert payload["selected"]["protocol"] == "morpho_blue"


def test_selection_exposes_robinhood_in_liquidation_scope(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    choices = available_chain_selections(feature="liquidation")
    market_ids = [choice.market_id for choice in choices]
    robinhood = resolve_chain_selection(chain_id=4663, feature="liquidation")
    testnet = resolve_chain_selection(chain_id=46630, feature="liquidation")

    assert "liquidation-4663-aave-v3" in market_ids
    assert "liquidation-46630-morpho-blue" in market_ids
    assert robinhood.market_id == "liquidation-4663-aave-v3"
    assert robinhood.legacy_market_id == "robinhood-liquidation-shadow"
    assert robinhood.capabilities["robinhood_shadow"] is True
    assert robinhood.capabilities["execution"] is False
    assert testnet.market_id == "liquidation-46630-morpho-blue"
    assert testnet.legacy_market_id == "robinhood-testnet-liquidation-shadow"
    assert testnet.global_chain_id == "chain:46630"
    assert testnet.capabilities["execution"] is False


def test_selection_exposes_robinhood_testnet_as_distinct_chain(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    testnet = resolve_chain_selection(chain_id=46630, feature="dex_arbitrage")

    assert testnet.market_id == "dex-arbitrage-46630-uniswap-v4"
    assert testnet.legacy_market_id == "robinhood-testnet-dex-paused"
    assert testnet.chain_id == 46630
    assert testnet.global_chain_id == "chain:46630"
    assert testnet.network == "robinhood-testnet"
    assert testnet.protocol == "uniswap_v4"
    assert testnet.capabilities["robinhood_shadow"] is False


def test_selection_prefers_feature_chain_protocol_triplet(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    liquidation = resolve_chain_selection(
        feature="liquidation",
        chain_id=56,
        protocol_id="venus_core",
    )
    dex = resolve_chain_selection(
        feature="dex_arbitrage",
        chain_id=56,
        protocol_id="pancakeswap_v3",
    )

    assert liquidation.market_id == "liquidation-56-venus-core"
    assert liquidation.legacy_market_id == "bnb-venus-core"
    assert dex.market_id == "dex-arbitrage-56-pancakeswap-v3"
    assert dex.legacy_market_id == "bnb-dex-arbitrage"


def test_selection_prefers_explicit_testnet_triplet_over_stale_mainnet_market(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    selected = resolve_chain_selection(
        feature="dex_arbitrage",
        chain_id=46630,
        protocol_id="uniswap_v4",
        market_id="robinhood-dex-shadow",
    )

    assert selected.market_id == "dex-arbitrage-46630-uniswap-v4"
    assert selected.chain_id == 46630


def test_selection_rejects_legacy_market_from_other_feature_when_triplet_is_explicit(monkeypatch):
    monkeypatch.setattr("core.chain_selection.supported_market_summaries", lambda _url=None: _rows())

    with pytest.raises(ChainSelectionError) as exc_info:
        resolve_chain_selection(
            market_id="bnb-venus-core",
            feature="dex_arbitrage",
            chain_id=56,
            protocol_id="pancakeswap_v3",
        )

    assert exc_info.value.code == "feature_mismatch"


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
