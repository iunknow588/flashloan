from __future__ import annotations

from pathlib import Path

from dex.intent_trade.chains.robinhood import v4_pool_index
from dex.intent_trade.chains.robinhood import v4_quote_adapter
from dex.intent_trade.chains.robinhood.v4_pool_index import (
    _iter_observation_pool_rows,
    default_v4_pool_index_path,
)
from bot.execution import uniswap_v4_sdk_adapter
from dex.runtime import dex_quote_evidence_common, dex_quote_evidence_helpers


def _module_text() -> str:
    return Path(v4_pool_index.__file__).read_text(encoding="utf-8")


def test_mainnet_v4_pool_index_does_not_import_testnet_internals():
    assert "robinhood_testnet" not in _module_text()
    assert "robinhood-testnet" not in _module_text()


def test_mainnet_v4_pool_index_path_is_mainnet_scoped(tmp_path):
    path = default_v4_pool_index_path()
    assert path is not None
    assert "robinhood" in str(path).replace("\\", "/")
    assert "robinhood-testnet" not in str(path).replace("\\", "/")
    assert str(path).replace("\\", "/").endswith("v4_pool_index/latest.json")


def test_iter_observation_pool_rows_collects_pool_ids_from_sequences():
    payload = {
        "observationSequences": [
            {
                "routePools": [
                    {"pool": "0x" + "a" * 64, "protocol": "uniswap_v4"},
                    {"poolId": "0x" + "b" * 64, "protocol": "uniswap_v4"},
                ],
                "poolIds": ["0x" + "c" * 64],
            }
        ]
    }
    rows = _iter_observation_pool_rows(payload)
    assert len(rows) == 3


def test_mainnet_v4_quote_adapter_rejects_testnet_chain():
    try:
        v4_quote_adapter._chain_rpc_urls("robinhood-testnet", 46630)
    except ValueError as exc:
        assert "robinhood mainnet v4" in str(exc)
        return
    raise AssertionError("mainnet V4 adapter should reject testnet chain scope")


def test_mainnet_v4_fee_bps_treats_dynamic_fee_flag_as_unknown():
    assert v4_quote_adapter._fee_bps(8388608) is None
    assert v4_quote_adapter._fee_bps(200) == 2
    assert v4_quote_adapter._fee_bps(50000) == 500


def test_mainnet_v4_quote_adapter_rejects_mismatched_token_without_rpc_call():
    request = {
        "network": "robinhood",
        "chainId": 4663,
        "pool": "0x1111111111111111111111111111111111111111",
        "tokenIn": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
        "tokenOut": "0x5fc5360D7Ff5E7E4762b0B8d0Db54B4D77b9dfFA",
        "amountInRaw": "1000000000000000",
    }
    try:
        v4_quote_adapter._resolve_v4_quote_context(request)
    except ValueError as exc:
        assert "requested hop tokens do not match" in str(exc)
        return
    raise AssertionError("mismatched token addresses should fail before pool manager state is read")


def test_mainnet_v4_quote_context_uses_static_pool_state_when_available(monkeypatch):
    request = {
        "network": "robinhood",
        "chainId": 4663,
        "pool": "0x1111111111111111111111111111111111111111",
        "tokenIn": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
        "tokenOut": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
        "amountInRaw": "1000000000000000",
        "fee": 200,
        "poolState": {
            "pool": "0x1111111111111111111111111111111111111111",
            "poolId": "0x1111111111111111111111111111111111111111",
            "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "token0": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
            "token1": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
            "token0Decimals": 18,
            "token1Decimals": 6,
            "tickSpacing": 4,
            "fee": 200,
            "hooks": "0x0000000000000000000000000000000000000000",
            "sqrtPriceX96": "3435248935698171264492576",
            "liquidity": "",
        },
    }

    def fail_live_state(*args, **kwargs):
        raise AssertionError("live extsload should not be called when PoolKey snapshot is complete")

    monkeypatch.setattr(v4_quote_adapter, "_read_live_pool_state", fail_live_state)

    context = v4_quote_adapter._resolve_v4_quote_context(request)

    assert context["liveState"]["source"] == "pool_index_static_fallback"
    assert context["rpcUrl"] in {"", None}
    assert context["poolManager"].lower() == "0x8366a39cc670b4001a1121b8f6a443a643e40951"
    assert context["zeroForOne"] is True


def test_v4_adapter_environment_isolated_by_robinhood_network(monkeypatch):
    resolved: list[str] = []

    def fake_resolve(reference):
        resolved.append(reference)
        return lambda request: {"status": "quoted"}

    monkeypatch.setattr(uniswap_v4_sdk_adapter, "_resolve_callable", fake_resolve)
    env = {
        "ROBINHOOD_MAINNET_V4_QUOTE_ADAPTER": "mainnet:adapter",
        "ROBINHOOD_TESTNET_V4_QUOTE_ADAPTER": "testnet:adapter",
    }

    uniswap_v4_sdk_adapter.load_v4_sdk_adapter(env, network="robinhood", chain_id=4663)
    uniswap_v4_sdk_adapter.load_v4_sdk_adapter(env, network="robinhood-testnet", chain_id=46630)

    assert resolved == ["mainnet:adapter", "testnet:adapter"]


def test_quote_evidence_v4_adapter_prefers_chain_specific_env(monkeypatch):
    resolved_modules: list[str] = []

    def make_module(module_name: str):
        module = type(module_name, (), {})
        module.adapter = lambda request: {"status": "quoted"}
        return module

    class _FakeImportlib:
        def import_module(self, module_name):
            resolved_modules.append(module_name)
            return make_module(module_name)

    monkeypatch.setattr(dex_quote_evidence_common, "importlib", _FakeImportlib())
    monkeypatch.setenv("ROBINHOOD_MAINNET_V4_QUOTE_ADAPTER", "mainnet:adapter")
    monkeypatch.setenv("ROBINHOOD_TESTNET_V4_QUOTE_ADAPTER", "testnet:adapter")
    monkeypatch.setenv("DEX_V4_QUOTE_ADAPTER", "generic:adapter")

    mainnet = dex_quote_evidence_helpers._load_v4_quote_adapter(network="robinhood", chain_id=4663)
    testnet = dex_quote_evidence_helpers._load_v4_quote_adapter(network="robinhood-testnet", chain_id=46630)

    assert callable(mainnet)
    assert callable(testnet)
    assert "mainnet" in resolved_modules
    assert "testnet" in resolved_modules
    assert "generic" not in resolved_modules
