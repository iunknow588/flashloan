"""Guards for the chain-scoped parameter layering.

A chain owns its addresses, RPC endpoints, quote adapters and broadcast
gates in ``flashloan/chains/<network>/chain.json``.  Environment variables
remain an operational override on top of that, never a second source of
truth.  These tests pin that precedence so a chain can never be configured
from two unrelated places.
"""

import json
from pathlib import Path

import pytest

from core.chain_data_registry import (
    CHAIN_DATA_ROOT,
    chain_flag,
    chain_path_value,
    chain_value,
)


ROBINHOOD = "robinhood"
ROBINHOOD_TESTNET = "robinhood-testnet"


def _chain_file(network: str) -> Path:
    return CHAIN_DATA_ROOT / network / "chain.json"


def _chain_json(network: str) -> dict:
    return json.loads(_chain_file(network).read_text(encoding="utf-8"))


def test_chain_value_reads_nested_chain_json_field():
    payload = _chain_json(ROBINHOOD)

    assert chain_value(ROBINHOOD, "rpc.primary") == payload["rpc"]["primary"]
    assert chain_value(ROBINHOOD, "contracts.v3_quoter") == payload["contracts"]["v3_quoter"]
    assert chain_value(ROBINHOOD, "officialV4.arb") == payload["officialV4"]["arb"]


def test_chain_value_env_override_wins_over_chain_json(monkeypatch):
    monkeypatch.setenv("TEST_ROBINHOOD_V3_QUOTER", "0xoverride")

    resolved = chain_value(
        ROBINHOOD,
        "contracts.v3_quoter",
        "TEST_ROBINHOOD_V3_QUOTER",
    )

    assert resolved == "0xoverride"


def test_chain_value_falls_back_to_chain_json_without_env(monkeypatch):
    monkeypatch.delenv("TEST_ROBINHOOD_V3_QUOTER", raising=False)
    expected = _chain_json(ROBINHOOD)["contracts"]["v3_quoter"]

    resolved = chain_value(
        ROBINHOOD,
        "contracts.v3_quoter",
        "TEST_ROBINHOOD_V3_QUOTER",
    )

    assert resolved == expected


def test_chain_value_returns_default_when_chain_and_env_are_absent():
    assert chain_value("no-such-chain", "rpc.primary", default="fallback") == "fallback"


def test_chain_value_joins_list_fields(monkeypatch):
    monkeypatch.delenv("TEST_ROBINHOOD_FALLBACKS", raising=False)
    payload = _chain_json(ROBINHOOD)

    resolved = chain_value(ROBINHOOD, "rpc.fallbacks", "TEST_ROBINHOOD_FALLBACKS")

    assert resolved == ",".join(payload["rpc"]["fallbacks"])


def test_chain_flag_reads_boolean_chain_json_gate(monkeypatch):
    monkeypatch.delenv("TEST_BROADCAST_GATE", raising=False)

    assert chain_flag(ROBINHOOD, "features.dex_arbitrage.allow_official_v4_broadcast") is False


def test_chain_flag_env_override_wins(monkeypatch):
    monkeypatch.setenv("TEST_BROADCAST_GATE", "true")

    assert chain_flag(
        ROBINHOOD,
        "features.dex_arbitrage.allow_official_v4_broadcast",
        "TEST_BROADCAST_GATE",
    ) is True


def test_chain_path_value_returns_nested_block_without_env(monkeypatch):
    monkeypatch.delenv("TEST_RPC_BLOCK", raising=False)

    rpc = chain_path_value(ROBINHOOD, "rpc")

    assert isinstance(rpc, dict)
    assert rpc["primary"] == _chain_json(ROBINHOOD)["rpc"]["primary"]


def test_broadcast_gate_defaults_to_false_in_every_chain_json():
    """The broadcast gate must be fail-closed unless explicitly enabled."""

    for chain_file in sorted(CHAIN_DATA_ROOT.glob("*/chain.json")):
        payload = json.loads(chain_file.read_text(encoding="utf-8"))
        feature = (payload.get("features") or {}).get("dex_arbitrage") or {}
        if "allow_official_v4_broadcast" in feature:
            assert feature["allow_official_v4_broadcast"] is False, chain_file


def test_chain_json_does_not_store_private_keys():
    """Private keys must never enter the versioned chain configuration."""

    for chain_file in sorted(CHAIN_DATA_ROOT.glob("*/chain.json")):
        raw = chain_file.read_text(encoding="utf-8").lower()
        for marker in ("private_key", "privatekey", "secret", "password", "jwt"):
            assert marker not in raw, f"{chain_file} must not contain {marker}"


def test_env_example_documents_the_layering_and_has_no_dead_keys():
    example = (
        Path(__file__).resolve().parents[2] / ".env.example"
    ).read_text(encoding="utf-8")

    assert "chain.json" in example
    assert "ROBINHOOD_HyperRPC_URL" not in example


def test_env_example_chain_scoped_overrides_are_empty_by_default():
    """Chain-owned values must not be duplicated into the shared env file."""

    example = (
        Path(__file__).resolve().parents[2] / ".env.example"
    ).read_text(encoding="utf-8")
    values = {}
    for line in example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()

    for key in (
        "ROBINHOOD_V3_QUOTER_ADDRESS",
        "ROBINHOOD_V3_QUOTER_ADDRESSES",
        "DEX_V3_QUOTER_ADDRESS",
        "ROBINHOOD_MAINNET_V4_QUOTE_ADAPTER",
        "ROBINHOOD_MAINNET_V4_SDK_ADAPTER",
        "ROBINHOOD_TESTNET_V4_QUOTE_ADAPTER",
        "ROBINHOOD_RPC_URL",
        "ROBINHOOD_TESTNET_RPC_URL",
    ):
        assert values.get(key, "") == "", f"{key} must stay empty in .env.example"


def test_robinhood_chain_json_declares_the_expected_homes():
    payload = _chain_json(ROBINHOOD)

    assert payload["rpc"]["primary"]
    assert payload["contracts"]["v3_quoter"]
    assert payload["officialV4"]["arb"]
    assert payload["officialV4"]["composer"]
    assert payload["defaults"]["v4_quote_adapter"]
    assert "v4_sdk_adapter" in payload["defaults"]
    assert "allow_official_v4_broadcast" in payload["features"]["dex_arbitrage"]


def test_robinhood_testnet_chain_json_declares_the_expected_homes():
    payload = _chain_json(ROBINHOOD_TESTNET)

    assert payload["rpc"]["primary"]
    assert payload["officialV4"]["arb"]
    assert payload["officialV4"]["composer"]
    assert payload["defaults"]["v4_quote_adapter"]
    assert "v4_sdk_adapter" in payload["defaults"]
    assert "allow_official_v4_broadcast" in payload["features"]["dex_arbitrage"]


@pytest.mark.parametrize("network", ["avalanche", "avalanche-testnet"])
def test_avalanche_chain_json_declares_aave_contract_homes(network):
    contracts = _chain_json(network)["contracts"]

    for key in ("aave_v3_pool", "aave_v3_data_provider", "aave_liquidation_data_provider"):
        assert key in contracts


def test_bnb_chain_json_declares_log_rpc_home():
    assert "log_fallbacks" in _chain_json("bnb")["rpc"]
