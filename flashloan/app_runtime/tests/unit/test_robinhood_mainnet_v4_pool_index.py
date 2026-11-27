from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from core.db.chain_database_context import chain_database_scope
from dex.intent_trade.chains.robinhood import v4_pool_index
from dex.intent_trade.chains.robinhood import v4_quote_adapter
from dex.intent_trade.chains.robinhood import v4_pool_capacity
from dex.intent_trade.chains.robinhood.v4_pool_index import (
    _iter_observation_pool_rows,
    default_v4_pool_index_path,
    keep_token_addresses_from_payload,
)
from bot.execution import uniswap_v4_sdk_adapter
from dex.runtime import dex_quote_evidence_common, dex_quote_evidence_helpers


def _module_text() -> str:
    return Path(v4_pool_index.__file__).read_text(encoding="utf-8")


CANONICAL_USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
COUNTERFEIT_USDG = "0x391f08bf88afa9c8e3ce9808cf73b5b11a86505e"


def _pool(token0: str, token1: str, decimals0: int, decimals1: int,
          symbol0: str = "ETH", symbol1: str = "USDG") -> dict:
    return {
        "token0": token0,
        "token1": token1,
        "token0Decimals": decimals0,
        "token1Decimals": decimals1,
        "token0Symbol": symbol0,
        "token1Symbol": symbol1,
    }


def _state(liquidity: int, sqrt_price_x96: int) -> dict:
    return {"sqrtPriceX96": str(sqrt_price_x96), "liquidity": str(liquidity)}


# At sqrt_price == 1 the full-range approximation gives
# amount0 = L / 1e18 and amount1 = L / 10**decimals1. L = 1e18 therefore
# yields exactly 1 ETH on the token0 side.
ONE_ETH_LIQUIDITY = 10 ** 18


def test_pool_index_has_no_hardcoded_eth_price_fallback():
    """The old $3000 default silently mis-priced every ETH pool."""
    assert "DEFAULT_ETH_PRICE_USD" not in _module_text()
    assert "ROBINHOOD_MAINNET_ETH_PRICE_USD\", \"3000" not in _module_text()


def test_true_reserve_tvl_uses_pool_own_decimals(monkeypatch):
    """USDG uses 6 decimals; assuming 18 would deflate the TVL by 1e12."""
    price = {"price": Decimal("2069.52"), "source": "binance_us",
             "observedAt": v4_pool_index._stamp(), "priceSource": "binance_us"}
    monkeypatch.setattr(v4_pool_index, "eth_price_usd", lambda **_: price)
    q96 = Decimal(2) ** 96
    sqrt_price_x96 = int(q96)
    # Liquidity chosen so token0 (ETH side, 18 decimals) is 1 ETH.
    liquidity = ONE_ETH_LIQUIDITY
    pool = _pool("0x0000000000000000000000000000000000000000", CANONICAL_USDG, 18, 6)
    metrics = v4_pool_index._liquidity_reserve_metrics(pool, _state(liquidity, sqrt_price_x96))
    assert metrics["hasLiquidity"] is True
    assert metrics["depthBasis"] == "true_reserve_estimate"
    assert metrics["priceSource"] == "binance_us"
    # 1 ETH at $2069.52 plus the USDG side at one dollar.
    reserve_usd0 = Decimal(metrics["reserveUsdToken0"])
    assert reserve_usd0 == Decimal("2069.52")
    reserve_usd1 = Decimal(metrics["reserveUsdToken1"])
    assert reserve_usd1 > 0
    tvl = Decimal(metrics["estimatedTvlUsd"])
    assert tvl == reserve_usd0 + reserve_usd1


def test_counterfeit_usdg_symbol_is_not_priced_as_a_dollar(monkeypatch):
    """A token that only claims the USDG symbol must not be priced at $1."""
    price = {"price": Decimal("2069.52"), "source": "binance_us",
             "observedAt": v4_pool_index._stamp(), "priceSource": "binance_us"}
    monkeypatch.setattr(v4_pool_index, "eth_price_usd", lambda **_: price)
    q96 = Decimal(2) ** 96
    pool = _pool("0x0000000000000000000000000000000000000000", COUNTERFEIT_USDG, 18, 18)
    metrics = v4_pool_index._liquidity_reserve_metrics(
        pool, _state(ONE_ETH_LIQUIDITY, int(q96)))
    # No dollar price may be inferred for the counterfeit token.
    assert metrics["token1ReserveUsd"] is None
    assert metrics["estimatedTvlUsd"] is None
    # The depth verdict does not depend on the USD reading: the pool still
    # has an on-chain capacity, so it is classified from that capacity.
    status = v4_pool_index._liquidity_depth_status(metrics)
    assert status["capacityRaw"] is not None
    # DEP-4.2: the verdict follows the binding-side capacity, which for a
    # pool with one ETH of liquidity is about 0.001 ETH. The USD reading
    # still plays no part in it.
    assert status["capacityBindingRaw"] is not None
    assert status["capacityBand"] == v4_pool_index.capacity_band(status["capacityBindingRaw"])


def test_missing_eth_price_refuses_to_publish_tvl(monkeypatch):
    """Without a usable price the pool stays unpriced instead of TVL=0."""
    monkeypatch.setattr(
        v4_pool_index, "eth_price_usd",
        lambda **_: {"price": None, "source": None, "observedAt": None,
                     "priceSource": None, "errors": ["all sources failed"]})
    q96 = Decimal(2) ** 96
    pool = _pool("0x0000000000000000000000000000000000000000", CANONICAL_USDG, 18, 6)
    metrics = v4_pool_index._liquidity_reserve_metrics(
        pool, _state(ONE_ETH_LIQUIDITY, int(q96)))
    assert metrics["estimatedTvlUsd"] is None
    assert metrics["priceSource"] is None
    # A missing USD feed does not change the depth verdict: the capacity is
    # denominated in the pool's own tokens and is computed on-chain.
    status = v4_pool_index._liquidity_depth_status(metrics)
    assert status["capacityRaw"] is not None
    # DEP-4.2: the verdict follows the binding-side capacity, which for a
    # pool with one ETH of liquidity is about 0.001 ETH. A missing USD feed
    # still plays no part in it.
    assert status["capacityBindingRaw"] is not None
    assert status["capacityBand"] == v4_pool_index.capacity_band(status["capacityBindingRaw"])


def test_eth_price_falls_back_to_the_next_source(monkeypatch):
    """The primary host may answer HTTP 451; the next source must be used."""
    calls: list[str] = []

    def fake_fetch(url: str, timeout: int = 10):
        calls.append(url)
        if "binance.us" in url:
            raise OSError("HTTP 451")
        if "kraken" in url:
            return {"result": {"ETHUSD": {"c": ["2069.51", "1"]}}}
        return {"ethereum": {"usd": 2069.5}}

    monkeypatch.setattr(v4_pool_index, "_fetch_json", fake_fetch)
    monkeypatch.setattr(v4_pool_index, "ETH_PRICE_CACHE",
                        {"price": None, "source": None, "observedAt": None})
    result = v4_pool_index.eth_price_usd(refresh=True)
    assert result["price"] == Decimal("2069.51")
    assert result["priceSource"] == "kraken"
    assert any("binance.us" in url for url in calls)
    assert any("kraken" in url for url in calls)


def test_eth_price_rejects_out_of_range_price(monkeypatch):
    def fake_fetch(url: str, timeout: int = 10):
        return {"price": "12"}

    monkeypatch.setattr(v4_pool_index, "_fetch_json", fake_fetch)
    monkeypatch.setattr(v4_pool_index, "ETH_PRICE_CACHE",
                        {"price": None, "source": None, "observedAt": None})
    result = v4_pool_index.eth_price_usd(refresh=True)
    assert result["price"] is None
    assert result["errors"]


def test_all_eth_price_sources_failing_refuses_a_price(monkeypatch):
    def fake_fetch(url: str, timeout: int = 10):
        raise OSError("network down")

    monkeypatch.setattr(v4_pool_index, "_fetch_json", fake_fetch)
    monkeypatch.setattr(v4_pool_index, "ETH_PRICE_CACHE",
                        {"price": None, "source": None, "observedAt": None})
    result = v4_pool_index.eth_price_usd(refresh=True)
    assert result["price"] is None
    assert result["priceSource"] is None
    assert len(result["errors"]) == len(v4_pool_index.ETH_PRICE_SOURCES)


def test_eth_price_reads_the_gateio_ticker_row(monkeypatch):
    """Gate.io answers with a list of ticker rows; the ETH row is the price."""
    calls: list[str] = []

    def fake_fetch(url: str, timeout: int = 10):
        calls.append(url)
        if "gateio" in url:
            return [{"currency_pair": "BTC_USDT", "last": "90000"},
                    {"currency_pair": "ETH_USDT", "last": "1703.87"}]
        raise OSError("network down")

    monkeypatch.setattr(v4_pool_index, "_fetch_json", fake_fetch)
    monkeypatch.setattr(v4_pool_index, "ETH_PRICE_CACHE",
                        {"price": None, "source": None, "observedAt": None})
    result = v4_pool_index.eth_price_usd(refresh=True)
    assert result["price"] == Decimal("1703.87")
    assert result["priceSource"] == "gateio"
    assert any("gateio" in url for url in calls)


def test_a_failed_price_fetch_is_not_retried_for_every_pool(monkeypatch):
    """A tick walks hundreds of pools; one failure has to be enough."""
    calls: list[str] = []

    def fake_fetch(url: str, timeout: int = 10):
        calls.append(url)
        raise OSError("network down")

    monkeypatch.setattr(v4_pool_index, "_fetch_json", fake_fetch)
    monkeypatch.setattr(v4_pool_index, "ETH_PRICE_CACHE",
                        {"price": None, "source": None, "observedAt": None})
    first = v4_pool_index.eth_price_usd()
    assert first["price"] is None
    assert calls
    second = v4_pool_index.eth_price_usd()
    assert second["price"] is None
    assert len(calls) == len(v4_pool_index.ETH_PRICE_SOURCES)


def test_a_stale_price_is_refreshed(monkeypatch):
    """A cached reading that grew stale is replaced, not handed out."""
    calls: list[str] = []

    def fake_fetch(url: str, timeout: int = 10):
        calls.append(url)
        return [{"currency_pair": "ETH_USDT", "last": "1703.87"}]

    monkeypatch.setattr(v4_pool_index, "_fetch_json", fake_fetch)
    monkeypatch.setattr(v4_pool_index, "ETH_PRICE_CACHE",
                        {"price": Decimal("1500"), "source": "gateio",
                         "observedAt": "2020-01-01T00:00:00Z"})
    result = v4_pool_index.eth_price_usd()
    assert result["price"] == Decimal("1703.87")
    assert calls


def test_depth_status_boundaries_match_the_rust_pool_manager():
    """Both sides classify the same binding-side capacity into the same band.

    DEP-4.2: the band boundaries are the shared capacity constants, and the
    operator is ``>=`` on both sides. The capacity that decides the band is
    the decimals-normalised binding side, because a raw unit is only
    comparable to another raw unit when both tokens share the same decimals.
    The USD reading is reported for observation but can never change the
    verdict.
    """
    deep = v4_pool_capacity.DEPTH_BAND_DEEP_CAPACITY
    medium = v4_pool_capacity.DEPTH_BAND_MEDIUM_CAPACITY
    shallow = v4_pool_capacity.DEPTH_BAND_SHALLOW_CAPACITY
    assert v4_pool_index._liquidity_depth_status(
        {"liquidityIndex": 1, "capacityRaw": 1, "capacityBindingRaw": deep})["label"] == "deep"
    assert v4_pool_index._liquidity_depth_status(
        {"liquidityIndex": 1, "capacityRaw": 1, "capacityBindingRaw": medium})["label"] == "medium"
    assert v4_pool_index._liquidity_depth_status(
        {"liquidityIndex": 1, "capacityRaw": 1, "capacityBindingRaw": shallow})["label"] == "thin"
    assert v4_pool_index._liquidity_depth_status(
        {"liquidityIndex": 1, "capacityRaw": 1,
         "capacityBindingRaw": shallow * 0.999})["label"] == "thin"
    # A USD reading alone never produces a depth verdict.
    assert v4_pool_index._liquidity_depth_status(
        {"liquidityIndex": 1, "estimatedTvlUsd": "1000000"})["label"] == "unpriced"


def test_binding_side_capacity_is_the_weaker_of_the_two_sides():
    """DEP-4.2: a pool is only as deep as its weaker side.

    The raw maximum of the two one-sided capacities hides a pool whose
    weaker side cannot absorb anything. The binding side is computed after
    each side is converted into its own token units, because a raw unit of a
    6-decimal stablecoin and a raw unit of an 18-decimal token are not the
    same quantity.
    """
    # One side cannot absorb anything: the pool is dust.
    assert v4_pool_capacity.binding_side_capacity(
        0, 1_000_000_000_000, 18, 6) == 0.0
    # The same raw amount means different things on the two scales.
    stable = v4_pool_capacity.binding_side_capacity(1_000_000, 1_000_000, 6, 6)
    token = v4_pool_capacity.binding_side_capacity(1_000_000, 1_000_000, 18, 18)
    assert stable == 1.0
    assert token == 1e-12
    assert stable > token
    # An unknown side cannot be decided.
    assert v4_pool_capacity.binding_side_capacity(None, 1, 18, 6) is None
    assert v4_pool_capacity.binding_side_capacity(1, 1, None, 6) is None
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


def test_keep_token_addresses_from_payload_ignores_zero_and_bad_rows():
    payload = {
        "ycZc": [
            {"address": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"},
            {"address": "0x0000000000000000000000000000000000000000"},
            {"address": "not-an-address"},
            {},
        ]
    }
    kept = keep_token_addresses_from_payload(payload)
    assert kept == {"0x5fc5360d0400a0fd4f2af552add042d716f1d168"}


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

    with chain_database_scope(4663, network="robinhood", source="test"):
        context = v4_quote_adapter._resolve_v4_quote_context(request)

    assert context["liveState"]["source"] == "pool_index_static_fallback"
    assert context["rpcUrl"] in {"", None}
    assert context["poolManager"].lower() == "0x8366a39cc670b4001a1121b8f6a443a643e40951"
    assert context["zeroForOne"] is True


def test_mainnet_v4_live_state_reads_liquidity_from_pool_state_offset_3():
    calls: list[bytes] = []

    class FakeExtsload:
        def __init__(self, slot: bytes):
            self.slot = slot

        def call(self, **kwargs):
            calls.append(bytes(self.slot))
            if len(calls) == 1:
                return b"\x00" * 31 + b"\x02"
            return b"\x00" * 31 + b"\x09"

    class FakeFunctions:
        extsload = FakeExtsload

    class FakeEth:
        def contract(self, **kwargs):
            return type("FakeContract", (), {"functions": FakeFunctions()})()

    class FakeW3:
        eth = FakeEth()

    state = v4_quote_adapter._read_live_pool_state_with_w3(
        w3=FakeW3(),
        rpc_url="rpc",
        pool_manager="0x" + "4" * 40,
        pool_id="0x" + "1" * 64,
        network="robinhood",
        chain_id=4663,
    )

    assert len(calls) == 2
    expected_liquidity_slot = (int.from_bytes(calls[0], "big") + 3).to_bytes(32, "big")
    assert calls[1] == expected_liquidity_slot
    assert state["liquidity"] == "9"


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



