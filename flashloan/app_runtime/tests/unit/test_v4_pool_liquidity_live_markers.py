"""Testnet V4 extsload liquidity marker regression tests."""

from __future__ import annotations

from dex.intent_trade.chains.robinhood_testnet import v4_pool_liquidity


def _pool(pool_id: str) -> dict:
    return {"poolId": pool_id}


def test_missing_sqrt_price_is_marked_zero_liquidity(monkeypatch) -> None:
    pool_id = "0x" + "aa" * 32
    pools = [_pool(pool_id)]

    def fake_states(**kwargs):
        return {
            pool_id: {
                "error": "robinhood testnet pool manager live slot0 missing sqrtPriceX96",
                "source": "pool_manager_extsload_live_state_batch",
            }
        }

    monkeypatch.setattr(v4_pool_liquidity, "_batch_read_live_pool_states", fake_states)
    result = v4_pool_liquidity._apply_extsload_liquidity(
        pools,
        w3=None,
        rpc_url="http://unused",
        pool_manager="0x" + "11" * 20,
        batch_size=10,
    )

    assert result["liquidityErrorCount"] == 0
    assert pools[0]["hasLiquidity"] is False
    assert pools[0]["liquidityError"] is None
    assert pools[0]["depthStatus"] == "no_liquidity"


def test_explicit_zero_liquidity_is_marked_zero(monkeypatch) -> None:
    pool_id = "0x" + "bb" * 32
    pools = [_pool(pool_id)]

    def fake_states(**kwargs):
        return {
            pool_id: {
                "error": "robinhood testnet pool manager live liquidity is zero",
                "source": "pool_manager_extsload_live_state_batch",
            }
        }

    monkeypatch.setattr(v4_pool_liquidity, "_batch_read_live_pool_states", fake_states)
    result = v4_pool_liquidity._apply_extsload_liquidity(
        pools,
        w3=None,
        rpc_url="http://unused",
        pool_manager="0x" + "22" * 20,
        batch_size=10,
    )

    assert result["liquidityMarkedPoolCount"] == 0
    assert pools[0]["hasLiquidity"] is False
    assert pools[0]["liquidityError"] is None


def test_generic_read_error_keeps_unknown(monkeypatch) -> None:
    pool_id = "0x" + "cc" * 32
    pools = [_pool(pool_id)]

    def fake_states(**kwargs):
        return {
            pool_id: {
                "error": "V4 pool state batch response missing slot0/liquidity",
                "source": "pool_manager_extsload_live_state_batch",
            }
        }

    monkeypatch.setattr(v4_pool_liquidity, "_batch_read_live_pool_states", fake_states)
    result = v4_pool_liquidity._apply_extsload_liquidity(
        pools,
        w3=None,
        rpc_url="http://unused",
        pool_manager="0x" + "33" * 20,
        batch_size=10,
    )

    assert result["liquidityErrorCount"] == 1
    assert pools[0]["hasLiquidity"] is None
    assert pools[0]["liquidityError"] is not None
