"""Read-only ETH/stock pool depth verification tests.

The old testnet conclusion assumed Swap-log silence meant zero liquidity.
This test locks in the corrected reading: live PoolManager liquidity is read
directly, a non-zero slot is not treated as usable depth, and evidence that
is still marked ``hasLiquidity=False`` while live state is non-zero must be
flagged as stale.
"""

from __future__ import annotations

import json
from pathlib import Path

from dex.tools.verify_robinhood_testnet_stock_pool_liquidity import (
    _load_stock_registry,
    select_eth_stock_pools,
    verify_stock_pool_liquidity,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
REGISTRY = REPO_ROOT / "flashloan" / "src" / "chains" / "robinhood-testnet" / "robinhood_stock_tokens.json"
EVIDENCE = (
    REPO_ROOT
    / "flashloan"
    / "src"
    / "runtime"
    / "evidence"
    / "robinhood-testnet"
    / "v4_pool_liquidity"
    / "latest.json"
)


def _pool(pool_id: str, symbol: str, *, address: str | None = None, fee: int = 3000, hooks: str = "0x" + "00" * 20) -> dict:
    currency1 = address or "0x" + "11" * 20
    return {
        "poolId": pool_id,
        "currency0": "0x0000000000000000000000000000000000000000",
        "currency1": currency1,
        "currency0Metadata": {
            "address": "0x0000000000000000000000000000000000000000",
            "decimals": 18,
            "symbol": "ETH",
        },
        "currency1Metadata": {
            "address": currency1,
            "decimals": 18,
            "symbol": symbol,
        },
        "fee": fee,
        "hooks": hooks,
        "hasLiquidity": None,
    }


def _state(sqrt_price_x96: int, liquidity: int, tick: int = 0) -> dict:
    return {
        "sqrtPriceX96": str(sqrt_price_x96),
        "liquidity": str(liquidity),
        "tick": tick,
        "lpFee": 0,
        "protocolFee": 0,
    }


def test_select_eth_stock_pools_only_returns_official_stock_pairs():
    stock_symbols = {"0x" + "aa" * 20: "AMD", "0x" + "bb" * 20: "AMZN"}
    stock_pool = _pool("0x" + "ca" * 32, "AMD", address="0x" + "aa" * 20)
    amzn_pool = _pool("0x" + "cb" * 32, "AMZN", address="0x" + "bb" * 20)
    unknown_pool = {
        **_pool("0x" + "cc" * 32, "UNKNOWN"),
        "currency1Metadata": {"symbol": "UNKNOWN"},
    }
    non_stock = {
        **_pool("0x" + "dd" * 32, "USDC"),
        "currency1": "0x" + "22" * 20,
    }
    selected = select_eth_stock_pools(
        [stock_pool, amzn_pool, unknown_pool, non_stock],
        stock_symbols,
    )
    assert [row["stockSymbol"] for row in selected] == ["AMD", "AMZN"]


def test_verify_stock_pool_liquidity_distinguishes_live_depth_from_usable_depth():
    liquid_pool = _pool("0x" + "aa" * 32, "AMZN", fee=100)
    thin_pool = _pool("0x" + "bb" * 32, "AMD", fee=3000)
    zero_pool = _pool("0x" + "cc" * 32, "AMZN", fee=500)
    dynamic_pool = _pool("0x" + "dd" * 32, "PLTR", fee=8388608, hooks="0x" + "33" * 20)
    fee_pool = _pool("0x" + "ee" * 32, "TSLA", fee=10000)

    # AMZN-100 carries ~482 unit liquidity at sqrt(1), AMD-3000 tiny dust.
    states = {
        liquid_pool["poolId"]: _state(2**96, 482_189_702_696_046_297_088, tick=0),
        thin_pool["poolId"]: _state(2**96, 100_000_000_000_000, tick=0),
        zero_pool["poolId"]: {"error": "pool manager live liquidity is zero"},
        dynamic_pool["poolId"]: {"error": "pool manager live liquidity is zero"},
        fee_pool["poolId"]: _state(2**96, 14_142_135_623_730_950, tick=0),
    }

    def fake_connect(network, chain_id):
        return object(), "https://fake.rpc"

    def fake_batch(**kwargs):
        return states

    result = verify_stock_pool_liquidity(
        pool_rows=[
            liquid_pool,
            thin_pool,
            zero_pool,
            dynamic_pool,
            fee_pool,
        ],
        rpc_url="https://fake.rpc",
        connect_func=fake_connect,
        batch_func=fake_batch,
    )
    assert result["poolCount"] == 5
    # Dynamic fee and 10000 fee pools are not candidates.
    assert result["candidatePoolCount"] == 3
    assert result["liveNonZeroCandidateCount"] == 2
    assert result["usableDepthCandidateCount"] == 1
    assert result["usableDepthPools"][0]["symbol"] == "AMZN"
    status_by_pool = {row["poolId"]: row["depthStatus"] for row in result["pools"]}
    assert status_by_pool[liquid_pool["poolId"]] == "deep"
    assert status_by_pool[thin_pool["poolId"]] == "thin"
    assert status_by_pool[zero_pool["poolId"]] == "no_liquidity"


def test_verify_flags_evidence_false_negative_when_live_state_nonzero():
    pool = _pool("0x" + "ab" * 32, "AMD")
    pool["hasLiquidity"] = False
    states = {pool["poolId"]: _state(2**96, 10**18, tick=0)}

    def fake_connect(network, chain_id):
        return object(), "https://fake.rpc"

    def fake_batch(**kwargs):
        return states

    result = verify_stock_pool_liquidity(
        pool_rows=[pool],
        rpc_url="https://fake.rpc",
        connect_func=fake_connect,
        batch_func=fake_batch,
    )
    assert result["evidenceStaleFalseNegativeCount"] == 1
    assert result["staleEvidencePools"][0]["poolId"] == pool["poolId"]


def test_real_testnet_evidence_has_five_official_stock_pools_and_ten_candidates():
    if not REGISTRY.exists() or not EVIDENCE.exists():
        return
    stock_symbols = _load_stock_registry(REGISTRY)
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    pools = evidence.get("pools") or []
    selected = select_eth_stock_pools(pools, stock_symbols)
    symbols = {row["stockSymbol"] for row in selected}
    assert {"AMD", "AMZN", "NFLX", "PLTR", "TSLA"} <= symbols
    candidates = [
        row
        for row in selected
        if int(row.get("fee") or 0) <= 3000
        and not int(row.get("fee") or 0) & 8388608
        and str(row.get("hooks") or "").lower()
        in {"", "0x0000000000000000000000000000000000000000"}
    ]
    assert len(candidates) == 10
    by_symbol = {row["stockSymbol"]: row["fee"] for row in candidates}
    assert by_symbol["AMD"] == 3000
    assert {row["fee"] for row in candidates if row["stockSymbol"] == "AMZN"} == {100, 500, 3000}
    assert by_symbol["NFLX"] == 3000
    assert {row["fee"] for row in candidates if row["stockSymbol"] == "PLTR"} == {500, 2500, 3000}
    assert {row["fee"] for row in candidates if row["stockSymbol"] == "TSLA"} == {500, 3000}
