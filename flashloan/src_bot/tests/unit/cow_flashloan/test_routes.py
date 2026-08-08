import json
from decimal import Decimal

import pytest

from cow_flashloan.routes import (
    CowToken,
    SUPPORTED_COW_NETWORKS,
    build_token_registry,
    cow_network_config,
    default_cow_owner,
    evaluate_cow_route,
    exchange_rate,
    from_units,
    load_cow_token_list,
    parse_route_path,
    rank_cow_routes,
    resolve_token,
    to_units,
)


def test_parse_route_path_accepts_common_notation():
    assert parse_route_path("USDC -> AAVE -> USDC") == ["USDC", "AAVE", "USDC"]
    assert parse_route_path(["USDC", "WAVAX"]) == ["USDC", "WAVAX"]


def test_units_round_trip_flooring():
    assert to_units("1.2345678", 6) == "1234567"
    assert from_units("1234567", 6) == "1.234567"
    assert exchange_rate(
        sell_amount_units="1000000000",
        sell_decimals=6,
        buy_amount_units="2000000000000000000",
        buy_decimals=18,
    ) == "0.002"


def test_resolve_token_rejects_ambiguous_or_unknown_symbol():
    registry = {"USDC": CowToken("USDC", "0x" + "1" * 40, 6, "test")}
    assert resolve_token("usdc", registry).symbol == "USDC"
    with pytest.raises(ValueError, match="unknown or ambiguous"):
        resolve_token("ABC", registry)


def test_cow_network_config_allows_sepolia_testnet(monkeypatch):
    monkeypatch.setenv("COW_NETWORK", "avalanche")

    config = cow_network_config(network="sepolia")

    assert config.network == "sepolia"
    assert config.chain_id == 11155111
    assert config.quote_api == "https://api.cow.fi/sepolia/api/v1/quote"
    assert config.testnet is True


def test_cow_network_config_matches_cow_sdk_enabled_networks():
    expected = {
        "ethereum": (1, "https://api.cow.fi/mainnet/api/v1/quote"),
        "gnosis": (100, "https://api.cow.fi/xdai/api/v1/quote"),
        "arbitrum_one": (42161, "https://api.cow.fi/arbitrum_one/api/v1/quote"),
        "base": (8453, "https://api.cow.fi/base/api/v1/quote"),
        "polygon": (137, "https://api.cow.fi/polygon/api/v1/quote"),
        "avalanche": (43114, "https://api.cow.fi/avalanche/api/v1/quote"),
        "bnb": (56, "https://api.cow.fi/bnb/api/v1/quote"),
        "linea": (59144, "https://api.cow.fi/linea/api/v1/quote"),
        "plasma": (9745, "https://api.cow.fi/plasma/api/v1/quote"),
        "ink": (57073, "https://api.cow.fi/ink/api/v1/quote"),
        "sepolia": (11155111, "https://api.cow.fi/sepolia/api/v1/quote"),
    }

    assert set(SUPPORTED_COW_NETWORKS) == set(expected)
    for network, (chain_id, quote_api) in expected.items():
        config = cow_network_config(network=network)
        assert config.chain_id == chain_id
        assert config.quote_api == quote_api
        if network != "sepolia":
            assert config.token_list_url.endswith(f"CoinGecko.{chain_id}.json")


def test_cow_network_config_accepts_common_aliases():
    assert cow_network_config(network="mainnet").network == "ethereum"
    assert cow_network_config(chain_id=100).network == "gnosis"
    assert cow_network_config(network="arbitrum").network == "arbitrum_one"
    assert cow_network_config(network="bsc").network == "bnb"


def test_cow_network_config_rejects_fuji_testnet(monkeypatch):
    monkeypatch.setenv("COW_NETWORK", "sepolia")

    with pytest.raises(ValueError, match="Sepolia"):
        cow_network_config(chain_id="43113")


def test_cow_network_config_is_not_selected_by_global_env(monkeypatch):
    monkeypatch.setenv("COW_NETWORK", "sepolia")
    monkeypatch.setenv("COW_CHAIN_ID", "11155111")

    config = cow_network_config()

    assert config.network == "avalanche"
    assert config.chain_id == 43114


def test_default_cow_owner_uses_executor_env(monkeypatch):
    owner = "0x" + "2" * 40
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", owner)

    assert default_cow_owner() == owner


def test_default_cow_owner_prefers_network_specific_owner(monkeypatch):
    sepolia_owner = "0x" + "3" * 40
    fallback_owner = "0x" + "4" * 40
    monkeypatch.setenv("COW_OWNER_SEPOLIA", sepolia_owner)
    monkeypatch.delenv("COW_OWNER_AVALANCHE", raising=False)
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", fallback_owner)

    assert default_cow_owner("sepolia") == sepolia_owner
    assert default_cow_owner("avalanche") == fallback_owner


def test_default_cow_owner_supports_added_mainnet_envs(monkeypatch):
    owner = "0x" + "5" * 40
    monkeypatch.setenv("COW_OWNER_BNB", owner)
    monkeypatch.delenv("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", raising=False)

    assert default_cow_owner("bsc") == owner


def test_load_cow_token_list_filters_selected_chain(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return json.dumps(
                {
                    "tokens": [
                        {
                            "chainId": 11155111,
                            "symbol": "USDC",
                            "address": "0x" + "1" * 40,
                            "decimals": 6,
                        },
                        {
                            "chainId": 43114,
                            "symbol": "USDC",
                            "address": "0x" + "2" * 40,
                            "decimals": 6,
                        },
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr("cow_flashloan.routes.urllib.request.urlopen", lambda *args, **kwargs: FakeResponse())

    tokens = load_cow_token_list("https://example.invalid/tokens.json", network="sepolia")

    assert [token.address for token in tokens] == ["0x" + "1" * 40]


def test_load_cow_token_list_uses_builtin_sepolia_tokens(monkeypatch):
    monkeypatch.setattr(
        "cow_flashloan.routes.urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected network fetch")),
    )

    tokens = load_cow_token_list(network="sepolia")

    assert {token.symbol for token in tokens} == {"WETH", "USDC", "COW"}


def test_sepolia_registry_does_not_mix_avalanche_aave_cache(tmp_path):
    cache_path = tmp_path / "aave.json"
    cache_path.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "symbol": "AVAX",
                        "address": "0x" + "a" * 40,
                        "decimals": 18,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    registry = build_token_registry(aave_cache_path=cache_path, cow_network="sepolia")

    assert "WETH" in registry
    assert "AVAX" not in registry


def test_evaluate_cow_route_ranks_by_final_amount(monkeypatch):
    usdc = CowToken("USDC", "0x" + "1" * 40, 6, "test")
    aave = CowToken("AAVE", "0x" + "2" * 40, 18, "test")
    registry = {"USDC": usdc, "AAVE": aave, usdc.address: usdc, aave.address: aave}

    def fake_quote(**kwargs):
        sell = kwargs["sell_token"].symbol
        buy = kwargs["buy_token"].symbol
        if (sell, buy) == ("USDC", "AAVE"):
            buy_amount = "2000000000000000000"
        else:
            buy_amount = "1100000000"
        return {"quote": {"buyAmount": buy_amount, "sellAmount": kwargs["sell_amount_units"], "feeAmount": "0"}}

    monkeypatch.setattr("cow_flashloan.routes.post_cow_quote", fake_quote)
    result = evaluate_cow_route(
        {"name": "r1", "path": ["USDC", "AAVE", "USDC"]},
        registry=registry,
        default_amount=Decimal("1000"),
    )
    assert result["viable"] is True
    assert result["final_amount"] == "1100"
    assert result["hops"][0]["sell_amount"] == "1000"
    assert result["hops"][0]["buy_amount"] == "2"
    assert result["hops"][0]["fee_amount"] == "0"
    assert result["hops"][0]["exchange_rate"] == "0.002"
    assert result["hops"][1]["sell_amount"] == "2"
    assert result["hops"][1]["buy_amount"] == "1100"
    assert result["hops"][1]["exchange_rate"] == "550"

    ranked = rank_cow_routes([result, {"name": "bad", "viable": False}])
    assert ranked[0]["name"] == "r1"


def test_evaluate_cow_route_reports_unknown_token_without_raising():
    usdc = CowToken("USDC", "0x" + "1" * 40, 6, "test")
    registry = {"USDC": usdc, usdc.address: usdc}

    result = evaluate_cow_route(
        {"name": "missing", "path": ["USDC", "ICX", "USDC"]},
        registry=registry,
        default_amount=Decimal("1000"),
    )

    assert result["viable"] is False
    assert result["path"] == ["USDC", "ICX", "USDC"]
    assert result["error"] == "unknown or ambiguous token: ICX"
