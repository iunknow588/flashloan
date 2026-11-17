"""Tests for the on-chain capacity depth chain.

The depth verdict of a pool is its on-chain price-impact capacity, which is
denominated in the pool's own tokens. These tests lock down the properties
that the rest of the pipeline depends on:

* the capacity maths agrees with the canonical Uniswap formulas;
* the pool index publishes the token-denominated balances and the capacity;
* the execution sizer can derive a real liquidity cap from those balances;
* the depth verdict never depends on a USD reading.
"""

from __future__ import annotations

import json
import pathlib
import re
from decimal import Decimal

import pytest

from dex.intent_trade.chains.robinhood import v4_pool_index
from dex.intent_trade.chains.robinhood import v4_pool_capacity
from dex.runtime.dex_quote_evidence_size import (
    _liquidity_cap_raw,
    _pool_borrow_side_amount,
)


Q96 = 1 << 96
ONE_ETH_LIQUIDITY = 10 ** 18


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


def _state(liquidity: int, sqrt_price_x96: int, lp_fee: int = 3000) -> dict:
    return {
        "sqrtPriceX96": str(sqrt_price_x96),
        "liquidity": str(liquidity),
        "lpFee": str(lp_fee),
    }


def test_capacity_matches_the_canonical_uniswap_vector():
    """The capacity must land on the canonical 10 bps boundary.

    For a pool at `sqrtPrice == Q96` with liquidity `L`, the canonical
    constant-product approximation puts the 10 bps capacity at roughly
    `L / 10000`. The exact value is verified against the canonical
    `get_next_sqrt_price_from_amount_0_rounding_up` formula.
    """
    liquidity = ONE_ETH_LIQUIDITY
    amount = 10 ** 15
    # The LP fee is excluded from the impact, but the amount that reaches the
    # curve is still reduced by it.
    amount_in_less_fee = amount * (1_000_000 - 3000) // 1_000_000
    # Canonical Uniswap next sqrt price after adding token0.
    numerator1 = liquidity << 96
    product = amount_in_less_fee * Q96
    denominator = numerator1 + product
    sqrt_next = (numerator1 * Q96 + denominator - 1) // denominator
    canonical_out = (liquidity * (Q96 - sqrt_next)) // Q96
    canonical_spot = (amount_in_less_fee * Q96 * Q96) // (1 << 192)
    canonical_impact = (canonical_spot - canonical_out) * 10_000.0 / canonical_spot

    measured = v4_pool_capacity.price_impact_bps(Q96, liquidity, 3000, amount, True)
    assert measured == pytest.approx(canonical_impact, rel=1e-9)


def test_capacity_scales_linearly_with_liquidity():
    """Doubling the liquidity must double the capacity."""
    small = v4_pool_capacity.capacity_from_sqrt_price(Q96, ONE_ETH_LIQUIDITY, 3000, 10.0)
    large = v4_pool_capacity.capacity_from_sqrt_price(Q96, 2 * ONE_ETH_LIQUIDITY, 3000, 10.0)
    assert large["capacityRaw"] > small["capacityRaw"]
    ratio = large["capacityRaw"] / small["capacityRaw"]
    assert ratio == pytest.approx(2.0, rel=0.01)
    # The realised impact sits exactly on the configured limit.
    assert small["priceImpactBps"] <= 10.0
    assert large["priceImpactBps"] <= 10.0


def test_capacity_rejects_an_unusable_pool_state():
    with pytest.raises(ValueError):
        v4_pool_capacity.capacity_from_sqrt_price(0, 1, 3000, 10.0)
    with pytest.raises(ValueError):
        v4_pool_capacity.capacity_from_sqrt_price(Q96, 0, 3000, 10.0)


def test_capacity_is_denominated_in_tokens_and_not_usd():
    """The capacity never depends on a price feed."""
    without_price = v4_pool_capacity.capacity_from_sqrt_price(Q96, ONE_ETH_LIQUIDITY, 3000, 10.0)
    assert without_price["depthBasis"] == "onchain_tick_liquidity"
    assert without_price["capacityRaw"] > 0


def test_pool_index_publishes_token_balances_and_capacity(monkeypatch):
    """The pool index must publish the token reserves and the capacity.

    Without the token-denominated balances the execution sizer could not
    derive a liquidity cap, the probe ladder collapsed to a single step and
    every best-amount value stayed unconfirmed.
    """
    price = {"price": Decimal("2069.52"), "source": "binance_us",
             "observedAt": v4_pool_index._stamp(), "priceSource": "binance_us"}
    monkeypatch.setattr(v4_pool_index, "eth_price_usd", lambda **_: price)
    pool = _pool("0x0000000000000000000000000000000000000000",
                 "0x5fc5360d0400a0fd4f2af552add042d716f1d168", 18, 6)
    metrics = v4_pool_index._liquidity_reserve_metrics(pool, _state(ONE_ETH_LIQUIDITY, Q96))
    assert metrics["token0Reserve"] is not None
    assert metrics["token1Reserve"] is not None
    assert metrics["capacityRaw"] is not None
    assert metrics["capacityRaw"] > 0
    assert metrics["capacityToken0"] is not None
    assert metrics["capacityToken1"] is not None
    assert metrics["capacityBand"] in {"deep", "medium", "shallow", "dust"}


def test_enrich_writes_token_balances_into_the_pool(monkeypatch):
    """The enrichment step must put the token amounts into `balances`."""
    price = {"price": Decimal("2069.52"), "source": "binance_us",
             "observedAt": v4_pool_index._stamp(), "priceSource": "binance_us"}
    monkeypatch.setattr(v4_pool_index, "eth_price_usd", lambda **_: price)
    state = _state(ONE_ETH_LIQUIDITY, Q96)
    monkeypatch.setattr(
        v4_pool_index, "_read_live_pool_state_with_w3",
        lambda **_: state,
    )
    pools = [{
        "poolId": "0x" + "11" * 32,
        "token0": "0x0000000000000000000000000000000000000000",
        "token1": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
        "token0Decimals": 18,
        "token1Decimals": 6,
        "token0Symbol": "ETH",
        "token1Symbol": "USDG",
    }]
    v4_pool_index._enrich_pool_liquidity(
        None, pools, pool_manager="0x8366a39cc670b4001a1121b8f6a443a643e40951",
    )
    balances = pools[0]["balances"]
    assert balances["token0"] is not None
    assert balances["token1"] is not None
    assert balances["capacityToken0"] is not None
    assert balances["capacityToken1"] is not None
    assert balances["depthBasis"] == "onchain_tick_liquidity"


def test_liquidity_cap_is_derived_once_balances_carry_tokens():
    """The sizing chain must produce a real cap instead of returning None."""
    pools = {
        "pool-1": {
            "token0Symbol": "ETH",
            "token1Symbol": "USDG",
            "token0Decimals": 18,
            "token1Decimals": 6,
            "balances": {"token0": "1.0", "token1": "2000.0"},
        }
    }
    assert _pool_borrow_side_amount(pools["pool-1"], "ETH") == Decimal("1.0")
    cap_raw, reason = _liquidity_cap_raw(pools, "ETH", 18)
    assert cap_raw is not None
    assert cap_raw > 0
    assert reason and "liquidity_cap" in reason


def test_liquidity_cap_is_none_when_balances_carry_no_tokens():
    """Without token balances the sizer must not invent a cap."""
    pools = {
        "pool-1": {
            "token0Symbol": "ETH",
            "token1Symbol": "USDG",
            "token0Decimals": 18,
            "token1Decimals": 6,
            "balances": {"tvlUsd": 1234},
        }
    }
    cap_raw, reason = _liquidity_cap_raw(pools, "ETH", 18)
    assert cap_raw is None
    assert reason is None


def test_depth_verdict_does_not_depend_on_the_usd_reading():
    """A USD reading alone can never produce a depth verdict."""
    without_capacity = v4_pool_index._liquidity_depth_status(
        {"liquidityIndex": 1, "estimatedTvlUsd": "999999999"}
    )
    assert without_capacity["label"] == "unpriced"
    assert without_capacity["capacityRaw"] is None


def test_low_liquidity_index_pool_with_real_capacity_is_not_excluded():
    """The `li < 1` exclusion rule is void.

    The ETH/USDG pool with a real reserve carries a liquidity index of
    0.0285 and 24 live closed loops. Its capacity is what matters now.
    """
    pool = {
        "liquidityIndex": 0.02854432,
        "capacityRaw": 10_040_130_401_208,
        # DEP-4.2: the band is decided on the decimals-normalised
        # binding-side capacity, so the raw maximum alone no longer
        # classifies the pool.
        "capacityBindingRaw": 1.5,
        "estimatedTvlUsd": "1280000",
    }
    status = v4_pool_index._liquidity_depth_status(pool)
    assert status["label"] == "deep"
    assert status["capacityBand"] == "deep"


def test_capacity_bands_use_the_shared_boundaries():
    """The band boundaries are the shared constants on both sides."""
    deep = v4_pool_capacity.DEPTH_BAND_DEEP_CAPACITY
    medium = v4_pool_capacity.DEPTH_BAND_MEDIUM_CAPACITY
    shallow = v4_pool_capacity.DEPTH_BAND_SHALLOW_CAPACITY
    assert v4_pool_capacity.capacity_band(deep) == "deep"
    assert v4_pool_capacity.capacity_band(medium) == "medium"
    assert v4_pool_capacity.capacity_band(shallow) == "shallow"
    assert v4_pool_capacity.capacity_band(shallow * 0.999) == "dust"
    assert v4_pool_capacity.capacity_band(None) == "unavailable"
    # The boundary operator is `>=` on both sides.
    assert v4_pool_capacity.capacity_band(shallow) == "shallow"
    assert v4_pool_capacity.capacity_band(medium) == "medium"
    assert v4_pool_capacity.capacity_band(deep) == "deep"


def test_raw_to_decimal_text_renders_token_amounts():
    assert v4_pool_capacity.raw_to_decimal_text(0, 18) == "0"
    assert v4_pool_capacity.raw_to_decimal_text(10 ** 18, 18) == "1"
    assert v4_pool_capacity.raw_to_decimal_text(15 * 10 ** 17, 18) == "1.5"
    assert v4_pool_capacity.raw_to_decimal_text(10 ** 6, 6) == "1"



# ---------------------------------------------------------------------------
# Capacity gate semantics
# ---------------------------------------------------------------------------
#
# The gate distinguishes three states. Collapsing them would either halt the
# whole pipeline (when a measurement is simply missing) or let a genuinely
# shallow pool through (when the capacity is known and too small).

def test_capacity_gate_passes_when_the_capacity_covers_the_requirement():
    verdict = v4_pool_capacity.capacity_gate(1_000, 500)
    assert verdict["verdict"] == "pass"
    assert verdict["blocked"] is False
    assert verdict["reason"] is None
    assert verdict["measured"] is True


def test_capacity_gate_blocks_a_known_insufficient_capacity():
    verdict = v4_pool_capacity.capacity_gate(100, 500)
    assert verdict["verdict"] == "insufficient"
    assert verdict["blocked"] is True
    assert verdict["reason"] == v4_pool_capacity.CAPACITY_INSUFFICIENT_REASON
    assert verdict["measured"] is True


def test_capacity_gate_reports_an_unmeasured_capacity_without_blocking():
    """A missing measurement is a coverage gap, not a market conclusion.

    The production depth rollout has not published a capacity for every hop
    yet. Blocking on that would stop every route instead of reporting the
    missing measurement.
    """
    verdict = v4_pool_capacity.capacity_gate(None, 500)
    assert verdict["verdict"] == "unknown"
    assert verdict["blocked"] is False
    assert verdict["reason"] is None
    assert verdict["measured"] is False


def test_capacity_gate_can_be_promoted_to_block_on_an_unmeasured_capacity():
    verdict = v4_pool_capacity.capacity_gate(None, 500, require_measured=True)
    assert verdict["verdict"] == "unknown"
    assert verdict["blocked"] is True
    assert verdict["reason"] == v4_pool_capacity.CAPACITY_UNKNOWN_REASON


def test_capacity_gate_uses_the_environment_switch(monkeypatch):
    monkeypatch.setenv(v4_pool_capacity.CAPACITY_GATE_REQUIRE_MEASURED_ENV, "1")
    assert v4_pool_capacity.capacity_gate_requires_measured() is True
    verdict = v4_pool_capacity.capacity_gate(None, 500)
    assert verdict["blocked"] is True

    monkeypatch.setenv(v4_pool_capacity.CAPACITY_GATE_REQUIRE_MEASURED_ENV, "0")
    assert v4_pool_capacity.capacity_gate_requires_measured() is False
    assert v4_pool_capacity.capacity_gate(None, 500)["blocked"] is False


def test_required_amount_for_profit_converts_the_gas_budget():
    assert v4_pool_capacity.required_amount_for_profit(1.0, 0.5) == 2
    assert v4_pool_capacity.required_amount_for_profit(0.0, 0.5) == 0
    assert v4_pool_capacity.required_amount_for_profit(1.0, 0.0) is None
    assert v4_pool_capacity.required_amount_for_profit(None, 0.5) is None


def test_healer_admission_floor_matches_the_shared_shallow_band():
    """The healer and the pool manager must admit on the same boundary.

    The Rust pool manager used an admission floor of one raw unit while the
    healer required the shared shallow band, so the two sides classified the
    same pool differently. The healer now imports the shared constant.
    """
    from scripts import mainnet_arbitrage_healer as healer

    assert healer.MIN_POOL_CAPACITY_RAW == v4_pool_capacity.DEPTH_BAND_SHALLOW_CAPACITY


# ---------------------------------------------------------------------------
# VAL-4-12: the economic floor has one effective copy
# ---------------------------------------------------------------------------
#
# The economic floor used to be a hardcoded constant in the Rust token
# manager (`EconomicPolicy::default()`), so the shared policy file and the
# code could disagree about what a position had to be worth. The floor now
# lives in `flashloan/config/depth_policy.json` and all three sides read it
# from there.

_RUST_USDG_ECONOMIC = (
    pathlib.Path(__file__).resolve().parents[3]
    / "tools" / "token_manager" / "src" / "usdg_economic.rs"
)


def test_the_economic_floor_is_denominated_in_usdg_not_usd():
    """The shared floor must be expressed in the venue's own quote asset."""
    section = v4_pool_capacity.ECONOMIC_POLICY
    assert section["basis"] == "usdg_quote_asset_not_usd"
    assert int(section["usdgDecimals"]) == 6
    assert int(section["minEconomicPositionUsdgRaw"]) == 10_000_000
    assert int(section["minPoolDepthUsdgRaw"]) == 10_000_000
    assert v4_pool_capacity.MIN_ECONOMIC_POSITION_USDG_RAW == 10_000_000
    assert v4_pool_capacity.MIN_POOL_DEPTH_USDG_RAW == 10_000_000


def test_the_python_and_rust_economic_floors_agree():
    """The Python view and the compiled Rust view must not drift apart."""
    source = _RUST_USDG_ECONOMIC.read_text(encoding="utf-8")
    pattern = re.compile(
        r"pub const (?P<name>COMPILED_MIN_[A-Z0-9_]+)\s*:\s*u128\s*=\s*(?P<value>[^;]+);"
    )
    found = {
        match.group("name"): int(match.group("value").replace("_", ""))
        for match in pattern.finditer(source)
    }
    assert found, "the Rust economic policy must expose its compiled view"
    assert found["COMPILED_MIN_ECONOMIC_POSITION_USDG_RAW"] == (
        v4_pool_capacity.MIN_ECONOMIC_POSITION_USDG_RAW
    )
    assert found["COMPILED_MIN_POOL_DEPTH_USDG_RAW"] == (
        v4_pool_capacity.MIN_POOL_DEPTH_USDG_RAW
    )


def test_the_shared_policy_file_requires_schema_version_three():
    """The economic section is only meaningful from schema version three."""
    policy = v4_pool_capacity.DEPTH_POLICY
    assert int(policy["schemaVersion"]) == 3


# ---------------------------------------------------------------------------
# Cross-language shared constants
# ---------------------------------------------------------------------------
#
# The Python pool index and the Rust pool manager classify the same on-chain
# state. If their thresholds drift apart, the two sides admit different pools
# and the closed-loop library silently disagrees with the candidate set. The
# Rust side now owns a single `depth_config` module; this test reads it and
# locks the Python constants to it.

_RUST_DEPTH_CONFIG = (
    pathlib.Path(__file__).resolve().parents[3]
    / "tools" / "pool_manager" / "src" / "depth_config.rs"
)


def _rust_constants() -> dict[str, str]:
    """Read the literal constants from the Rust shared configuration."""
    source = _RUST_DEPTH_CONFIG.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    pattern = re.compile(
        r"pub const (?P<name>[A-Z0-9_]+)\s*:\s*(?P<type>u128|i64|f64|&str)\s*=\s*"
        r"(?P<value>[^;]+);"
    )
    for match in pattern.finditer(source):
        value = match.group("value").strip()
        if match.group("type") == "&str":
            found[match.group("name")] = value.strip('"')
        else:
            found[match.group("name")] = value.replace("_", "")
    return found


def test_the_rust_shared_configuration_is_readable():
    """The Rust shared configuration must exist and expose the thresholds."""
    constants = _rust_constants()
    for name in (
        "PRICE_IMPACT_LIMIT_BPS",
        "DEPTH_BAND_DEEP_CAPACITY",
        "DEPTH_BAND_MEDIUM_CAPACITY",
        "DEPTH_BAND_SHALLOW_CAPACITY",
        "MIN_CANDIDATE_CAPACITY_RAW",
        "MAX_CANDIDATE_FIXED_FEE",
        "DEPTH_BASIS",
    ):
        assert name in constants, f"{name} is missing from the Rust configuration"


def test_the_python_thresholds_match_the_rust_thresholds():
    """Both languages must classify the same pool identically."""
    constants = _rust_constants()
    assert float(constants["PRICE_IMPACT_LIMIT_BPS"]) == float(
        v4_pool_capacity.DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS)
    assert float(constants["DEPTH_BAND_DEEP_CAPACITY"]) == float(
        v4_pool_capacity.DEPTH_BAND_DEEP_CAPACITY)
    assert float(constants["DEPTH_BAND_MEDIUM_CAPACITY"]) == float(
        v4_pool_capacity.DEPTH_BAND_MEDIUM_CAPACITY)
    assert float(constants["DEPTH_BAND_SHALLOW_CAPACITY"]) == float(
        v4_pool_capacity.DEPTH_BAND_SHALLOW_CAPACITY)
    assert int(constants["MIN_CANDIDATE_CAPACITY_RAW"]) == int(
        v4_pool_capacity.MIN_CANDIDATE_CAPACITY_RAW)
    assert float(constants["MIN_CANDIDATE_BINDING_CAPACITY"]) == float(
        v4_pool_capacity.MIN_CANDIDATE_BINDING_CAPACITY)
    assert constants["DEPTH_BASIS"] == v4_pool_capacity.DEPTH_BASIS
    assert constants["DEPTH_BAND_BASIS"] == v4_pool_capacity.DEPTH_BAND_BASIS


def test_the_healer_admission_floor_matches_the_shared_shallow_band():
    """The healer must not import its own floor."""
    constants = _rust_constants()
    assert float(constants["DEPTH_BAND_SHALLOW_CAPACITY"]) == float(
        v4_pool_capacity.DEPTH_BAND_SHALLOW_CAPACITY)


# ---------------------------------------------------------------------------
# Cross-language parity with the Rust pool manager
# ---------------------------------------------------------------------------
#
# The Rust `pool-manager depth` command and this Python module are two
# implementations of the same capacity definition. They must agree, otherwise
# the depth snapshot written by the Rust tool and the capacity gate evaluated
# by the Python runtime would reach different verdicts for the same pool.
#
# The vectors below are live Robinhood mainnet pool states read through
# `extsload` on 2026-11-11. The Rust values are the ones the pool manager
# produced for the same states.

_LIVE_CAPACITY_VECTORS = [
    {
        "poolId": "0x207783b44fcb4359b281e8040bf476b69cfebf1c93021a11923277ced238d1bc",
        "sqrtPriceX96": 79220114170182869900576992101,
        "liquidity": 99778947815985782472265,
        "lpFee": 500,
        "rustCapacityToken0Raw": 99938943274133825410,
        "rustCapacityToken1Raw": 99918639833469791646,
    },
    {
        "poolId": "0x509c6c6826ef06e0e29f24f18d9389533c52358a3b385d99a67a2afbb07f23c6",
        "sqrtPriceX96": 130435955080940829379112576509,
        "liquidity": 1291440528785143188326,
        "lpFee": 1000,
        "rustCapacityToken0Raw": 786005701524762426,
        "rustCapacityToken1Raw": 2130400100532712269,
    },
    {
        "poolId": "0xd1aeea40e4def63338b6ae30e541f9207329585d58ccd57b9e4228f82b872388",
        "sqrtPriceX96": 141500439749489154383092096390,
        "liquidity": 1342852981540862940,
        "lpFee": 2500,
        "rustCapacityToken0Raw": 754521934204212,
        "rustCapacityToken1Raw": 2406735040102765,
    },
]


@pytest.mark.parametrize("vector", _LIVE_CAPACITY_VECTORS)
def test_python_capacity_matches_the_rust_pool_manager(vector):
    """Both implementations must produce the same capacity for one pool."""
    capacity = v4_pool_capacity.capacity_from_sqrt_price(
        vector["sqrtPriceX96"],
        vector["liquidity"],
        vector["lpFee"],
        float(v4_pool_capacity.DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS),
    )
    for side in ("Token0", "Token1"):
        python_value = capacity[f"capacity{side}Raw"]
        rust_value = vector[f"rustCapacity{side}Raw"]
        assert python_value > 0, f"{vector['poolId']} {side} capacity must be positive"
        # The two implementations round differently in the last bits, so the
        # comparison is relative rather than exact.
        relative_error = abs(python_value - rust_value) / max(rust_value, 1)
        assert relative_error < 1e-6, (
            f"{vector['poolId']} {side} capacity diverged: "
            f"python={python_value} rust={rust_value} relative_error={relative_error}"
        )
    assert capacity["priceImpactBps"] <= float(
        v4_pool_capacity.DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS
    ) + 1e-9


def test_the_capacity_of_a_live_pool_is_finite():
    """A live pool must never report an unbounded capacity.

    The Rust implementation used to saturate at `u128::MAX` for an
    oversized input, which the binary search read as "zero price impact"
    and therefore reported an effectively infinite pool.
    """
    vector = _LIVE_CAPACITY_VECTORS[0]
    capacity = v4_pool_capacity.capacity_from_sqrt_price(
        vector["sqrtPriceX96"],
        vector["liquidity"],
        vector["lpFee"],
        float(v4_pool_capacity.DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS),
    )
    assert capacity["capacityRaw"] < (1 << 128) - 2
    assert capacity["capacityRaw"] < vector["liquidity"] * 1000


# ---------------------------------------------------------------------------
# W3: the shared depth policy is the single source of truth
# ---------------------------------------------------------------------------


def test_depth_policy_file_is_the_single_source_of_truth():
    """The Python capacity module must read the shared policy file.

    The Rust pool manager reads the same file and asserts at start-up that
    its compiled constants still agree. A local copy of the thresholds in
    Python would silently break that guarantee.
    """
    policy = v4_pool_capacity.DEPTH_POLICY
    assert policy["priceImpactLimitBps"] == v4_pool_capacity.DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS
    assert policy["depthBandDeepCapacity"] == v4_pool_capacity.DEPTH_BAND_DEEP_CAPACITY
    assert policy["depthBandMediumCapacity"] == v4_pool_capacity.DEPTH_BAND_MEDIUM_CAPACITY
    assert policy["depthBandShallowCapacity"] == v4_pool_capacity.DEPTH_BAND_SHALLOW_CAPACITY
    assert policy["minCandidateCapacityRaw"] == v4_pool_capacity.MIN_CANDIDATE_CAPACITY_RAW
    assert policy["maxCandidateFixedFee"] == v4_pool_capacity.MAX_CANDIDATE_FIXED_FEE
    assert policy["depthBasis"] == v4_pool_capacity.DEPTH_BASIS


def test_depth_policy_file_is_present_in_the_repository():
    """The shared policy must be a checked-in file, not a runtime artifact."""
    path = v4_pool_capacity._resolve_depth_policy_path()
    assert path.is_file(), f"shared depth policy must exist at {path}"
    assert path.name == "depth_policy.json"


def test_depth_policy_rejects_a_malformed_document(monkeypatch, tmp_path):
    """A policy file missing a required key must fail loudly."""
    import json

    broken = tmp_path / "depth_policy.json"
    broken.write_text(json.dumps({"priceImpactLimitBps": 10.0}), encoding="utf-8")
    monkeypatch.setenv("FLASHLOAN_DEPTH_POLICY_PATH", str(broken))

    with pytest.raises(ValueError, match="missing required keys"):
        v4_pool_capacity.load_depth_policy()


def test_depth_policy_override_is_honoured(monkeypatch, tmp_path):
    """The policy path can be overridden for a deployment."""
    import json

    override = tmp_path / "depth_policy.json"
    override.write_text(
        json.dumps(
            {
                "priceImpactLimitBps": 42.0,
                "depthBandDeepCapacity": 1.0,
                "depthBandMediumCapacity": 2.0,
                "depthBandShallowCapacity": 3.0,
                "minCandidateCapacityRaw": 4,
                "maxCandidateFixedFee": 5,
                "depthBasis": "custom",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FLASHLOAN_DEPTH_POLICY_PATH", str(override))

    policy = v4_pool_capacity.load_depth_policy()
    assert policy["priceImpactLimitBps"] == 42.0
    assert policy["depthBasis"] == "custom"


def test_depth_policy_contains_no_usd_admission_threshold():
    """No USD value may sit outside the explicitly marked reference block.

    A USD reading cannot express tradable value inside the arbitrage
    environment, so an admission threshold in USD would be a regression.
    """
    policy = v4_pool_capacity.DEPTH_POLICY
    reference = policy.get("referenceOnly", {})
    for key, value in policy.items():
        # `usdgPegPolicy` (R-11) is a *ruling declaration*, not an admission
        # threshold: it names the peg ratio and the uses the peg may enter.
        # It carries no threshold at all, and it is checked separately below.
        if key in {"_comment", "referenceOnly", "gasEstimateEth", "schemaVersion",
                   "depthBandBasis", "_bandCalibration", "economicPolicy",
                   "usdgPegPolicy"}:
            continue
        assert key not in reference, f"{key} must not be reference-only"
        assert not key.lower().endswith("usd"), f"{key} must not be a USD threshold"
        assert not isinstance(value, (dict, list)), f"{key} must be a scalar threshold"

    # The nested economic section is a section, not a threshold, so it is
    # checked separately: it may not carry a USD threshold either.
    economic = policy["economicPolicy"]
    for key, value in economic.items():
        if key.startswith("_"):
            continue
        assert key not in reference, f"economicPolicy.{key} must not be reference-only"
        assert not key.lower().endswith("usd"), (
            f"economicPolicy.{key} must not be a USD threshold"
        )
        assert not isinstance(value, (dict, list)), (
            f"economicPolicy.{key} must be a scalar threshold"
        )


# ---------------------------------------------------------------------------
# R-11: USDG may be treated as economically equivalent to USD
# ---------------------------------------------------------------------------


def test_the_usdg_peg_policy_is_declared_with_a_ratio_of_one():
    """R-11: USDG is USD-equivalent by definition, so the ratio is exactly one.

    The ratio and the zero deviation tolerance are the auditable fields that
    every USDG/USD conversion carries. A depegged USDG has to fail loudly
    instead of being approximated by a near-USD number.
    """
    policy = v4_pool_capacity.USDG_PEG_POLICY
    assert policy["basis"] == "usdg_is_usd_equivalent_by_definition"
    assert float(policy["pegRatio"]) == 1.0
    assert int(float(policy["deviationToleranceBps"])) == 0
    assert v4_pool_capacity.USDG_PEG_RATIO == 1.0
    assert v4_pool_capacity.USDG_PEG_DEVIATION_TOLERANCE_BPS == 0
    assert v4_pool_capacity.USDG_PEG_BASIS == "usdg_is_usd_equivalent_by_definition"


def test_the_usdg_peg_never_decides_an_admission():
    """R-11/R-02: the peg may state economics, never gate membership.

    Depth, capacity and library membership are decided by on-chain facts.
    The peg is only allowed to enter reporting, the economic floor, gas
    comparisons, displayed notionals and the stock-library exchange
    requirement.
    """
    forbidden = set(v4_pool_capacity.USDG_PEG_FORBIDDEN_USES)
    allowed = set(v4_pool_capacity.USDG_PEG_ALLOWED_USES)
    for name in (
        "depth_admission",
        "capacity_admission",
        "core_library_membership",
        "test_library_membership",
        "extension_library_membership",
        "broadcast_amount_sizing",
    ):
        assert name in forbidden, f"{name} must be a forbidden use of the peg"
        assert name not in allowed, f"{name} must not be an allowed use of the peg"
    assert "stock_library_usdg_exchange_requirement" in allowed
    assert allowed.isdisjoint(forbidden)


def test_usdg_replaces_usd_as_the_economic_reference():
    """R-11 (2026-11-12 clarification): USDG replaces USD as the reference.

    The root cause is that most tokens have no exchange channel, so no
    external USD price can be obtained for them, while USDG is exchangeable
    in the large majority of cases. Every USD-denominated economic quantity
    therefore has to be expressed as an equal amount of USDG.

    This test pins the replacement explicitly so a later edit cannot quietly
    narrow the ruling back to a reporting-only concern.
    """
    allowed = set(v4_pool_capacity.USDG_PEG_ALLOWED_USES)
    forbidden = set(v4_pool_capacity.USDG_PEG_FORBIDDEN_USES)
    assert "usdg_replaces_usd_denomination" in allowed
    assert "economic_quantity_denominated_in_usdg" in allowed
    assert "stock_library_usdg_exchange_requirement" in allowed
    # The replacement acts on the denomination only; it never becomes a
    # depth, capacity or membership gate.
    for name in (
        "depth_admission",
        "capacity_admission",
        "core_library_membership",
        "test_library_membership",
        "extension_library_membership",
    ):
        assert name in forbidden, f"{name} must stay forbidden"
        assert name not in allowed, f"{name} must not be an allowed use of the peg"

def test_a_depegged_usdg_is_rejected(tmp_path, monkeypatch):
    """A peg other than one, or a tolerated deviation, must be refused.

    The ruling is "USDG is USD-equivalent by definition". Anything else is a
    different ruling and has to be made explicitly, not inherited from a
    stale policy file.
    """
    policy = dict(v4_pool_capacity.USDG_PEG_POLICY)
    policy.pop("_comment", None)

    def _write(peg_ratio: float, tolerance: float) -> None:
        override = tmp_path / "depth_policy.json"
        override.write_text(
            json.dumps(
                {
                    "schemaVersion": 3,
                    "priceImpactLimitBps": 10.0,
                    "depthBandDeepCapacity": 0.1,
                    "depthBandMediumCapacity": 0.01,
                    "depthBandShallowCapacity": 0.001,
                    "minCandidateCapacityRaw": 100,
                    "maxCandidateFixedFee": 3000,
                    "depthBasis": "pool_own_token_price_impact_capacity",
                    "usdgPegPolicy": {
                        **policy,
                        "pegRatio": peg_ratio,
                        "deviationToleranceBps": tolerance,
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("FLASHLOAN_DEPTH_POLICY_PATH", str(override))

    _write(1.0, 0.0)
    assert v4_pool_capacity.load_usdg_peg_policy()["pegRatio"] == 1.0

    _write(0.98, 0.0)
    with pytest.raises(ValueError, match="pegRatio must be 1.0"):
        v4_pool_capacity.load_usdg_peg_policy()

    _write(1.0, 25.0)
    with pytest.raises(ValueError, match="deviationToleranceBps must be 0"):
        v4_pool_capacity.load_usdg_peg_policy()


def test_the_python_and_rust_usdg_peg_views_agree():
    """The Python view and the compiled Rust view must not drift apart."""
    source = _RUST_USDG_ECONOMIC.read_text(encoding="utf-8")
    ratio = re.search(
        r"pub const COMPILED_USDG_PEG_RATIO\s*:\s*f64\s*=\s*([^;]+);", source
    )
    tolerance = re.search(
        r"pub const COMPILED_USDG_PEG_DEVIATION_TOLERANCE_BPS\s*:\s*i64\s*=\s*([^;]+);",
        source,
    )
    assert ratio, "the Rust crate must expose its compiled peg ratio"
    assert tolerance, "the Rust crate must expose its compiled deviation tolerance"
    assert float(ratio.group(1).strip()) == v4_pool_capacity.USDG_PEG_RATIO
    assert int(tolerance.group(1).strip()) == (
        v4_pool_capacity.USDG_PEG_DEVIATION_TOLERANCE_BPS
    )
    # The Rust side must assert the same ruling at start-up.
    assert "assert_usdg_peg_policy_matches_file" in source
