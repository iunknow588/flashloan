"""Regression tests for tri-state depth handling across Robinhood surfaces.

Unknown depth evidence must stay unknown (``None``) instead of silently
becoming ``has_liquidity=0`` / ``usable_for_observation=False``, and a later
unknown refresh must not erase previously confirmed evidence.
"""

from __future__ import annotations

import json

from core.db.storage_business_schema import ensure_business_schema
from core.db.storage_chain_core import seed_chain_tokens
from core.db.storage_dex_candidate import (
    load_dex_candidate_pools,
    replace_dex_candidate_tables,
)
from core.db.storage_market_observation import (
    load_market_observation,
    replace_market_observation_tables,
)
from dex.intent_trade.chains.robinhood import flash_loan_pool_views
from dex.intent_trade.chains.robinhood import v4_quote_adapter as mainnet_quote_adapter
from dex.intent_trade.chains.robinhood_testnet import dex_pool_candidates as testnet_candidates
from dex.intent_trade.chains.robinhood_testnet import v4_quote_adapter as testnet_quote_adapter
from market.binance_market.dex_quote_surface_routes_builder import _pool_usable_state


ETH = "0x0000000000000000000000000000000000000000"
TOKEN = "0x0000000000000000000000000000000000000001"
ZERO_HOOKS = "0x0000000000000000000000000000000000000000"


def _seed_tokens(db: str, network: str, chain_id: int) -> None:
    seed_chain_tokens(
        db,
        network=network,
        chain_id=chain_id,
        token_rows=[
            {"token_address": ETH, "token_symbol": "ETH", "token_decimals": 18, "is_native": 1},
            {"token_address": TOKEN, "token_symbol": "TOKEN", "token_decimals": 18},
        ],
    )


def _pool(pool_id: str, **overrides: object) -> dict:
    row = {
        "pool_id": pool_id,
        "pool_identity_mode": "v4_pool_id",
        "pool_manager": ETH,
        "pool_key": {
            "currency0": ETH,
            "currency1": TOKEN,
            "fee": 3000,
            "hooks": ZERO_HOOKS,
        },
        "token0": ETH,
        "token1": TOKEN,
        "fee": 3000,
        "hooks": ZERO_HOOKS,
        "tick_spacing": 60,
    }
    row.update(overrides)
    return row


def test_candidate_storage_keeps_unknown_depth_and_preserves_confirmed(tmp_path):
    db = str(tmp_path / "candidate.sqlite3")
    ensure_business_schema(db)
    _seed_tokens(db, "robinhood", 4663)
    pool_id = "0x" + "aa" * 32

    replace_dex_candidate_tables(
        db,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        anchor_scope="yc_zc",
        anchor_symbol="ETH",
        tokens=[],
        pairs=[],
        pools=[_pool(pool_id, has_liquidity=None, usable_for_observation=None)],
        recommendations=[],
    )
    rows = load_dex_candidate_pools(
        db,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        anchor_symbol="ETH",
    )["pools"]
    assert rows[0]["has_liquidity"] is None
    assert rows[0]["usable_for_observation"] is None

    replace_dex_candidate_tables(
        db,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        anchor_scope="yc_zc",
        anchor_symbol="ETH",
        tokens=[],
        pairs=[],
        pools=[
            _pool(
                pool_id,
                has_liquidity=True,
                usable_for_observation=True,
                liquidity="1000000000000000000000",
                liquidity_index=1000.0,
                depth_status="deep",
            )
        ],
        recommendations=[],
    )
    rows = load_dex_candidate_pools(
        db,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        anchor_symbol="ETH",
    )["pools"]
    assert rows[0]["has_liquidity"] == 1
    assert rows[0]["usable_for_observation"] == 1

    # A later refresh without depth evidence must not erase confirmed state.
    replace_dex_candidate_tables(
        db,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        anchor_scope="yc_zc",
        anchor_symbol="ETH",
        tokens=[],
        pairs=[],
        pools=[_pool(pool_id, has_liquidity=None, usable_for_observation=None)],
        recommendations=[],
    )
    rows = load_dex_candidate_pools(
        db,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        anchor_symbol="ETH",
    )["pools"]
    assert rows[0]["has_liquidity"] == 1
    assert rows[0]["usable_for_observation"] == 1

    # Explicit false is still authoritative.
    replace_dex_candidate_tables(
        db,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        anchor_scope="yc_zc",
        anchor_symbol="ETH",
        tokens=[],
        pairs=[],
        pools=[_pool(pool_id, has_liquidity=False, usable_for_observation=False)],
        recommendations=[],
    )
    rows = load_dex_candidate_pools(
        db,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
        anchor_symbol="ETH",
    )["pools"]
    assert rows[0]["has_liquidity"] == 0
    assert rows[0]["usable_for_observation"] == 0


def test_market_observation_storage_keeps_unknown_depth_and_preserves_confirmed(tmp_path):
    db = str(tmp_path / "observation.sqlite3")
    ensure_business_schema(db)
    _seed_tokens(db, "robinhood-testnet", 46630)
    pool_id = "0x" + "bb" * 32

    def replace(liquidity_rows: list[dict]) -> None:
        replace_market_observation_tables(
            db,
            network="robinhood-testnet",
            chain_id=46630,
            protocol="uniswap_v4",
            anchor_scope="y_z",
            anchor_symbol="ETH",
            directions=[],
            pools=[],
            liquidity=liquidity_rows,
        )

    replace([{"pool_id": pool_id, "has_liquidity": None, "recent_swap_active": None}])
    row = load_market_observation(
        db,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        anchor_symbol="ETH",
    )["liquidity"][0]
    assert row["has_liquidity"] is None
    assert row["recent_swap_active"] is None

    replace(
        [
            {
                "pool_id": pool_id,
                "has_liquidity": True,
                "recent_swap_active": True,
                "liquidity": "1000000000000000000000",
                "liquidity_index": 1000.0,
                "depth_status": "deep",
            }
        ]
    )
    row = load_market_observation(
        db,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        anchor_symbol="ETH",
    )["liquidity"][0]
    assert row["has_liquidity"] == 1
    assert row["recent_swap_active"] == 1

    replace([{"pool_id": pool_id, "has_liquidity": None, "recent_swap_active": None}])
    row = load_market_observation(
        db,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        anchor_symbol="ETH",
    )["liquidity"][0]
    assert row["has_liquidity"] == 1
    assert row["recent_swap_active"] == 1


def _quote_pool(pool_id: str, *, has_liquidity: object, hooks: str = ZERO_HOOKS) -> dict:
    row = {
        "pool": pool_id,
        "poolId": pool_id,
        "token0": "0x" + "1" * 40,
        "token1": "0x" + "2" * 40,
        "fee": 500,
        "hooks": hooks,
        "poolKey": {
            "currency0": "0x" + "1" * 40,
            "currency1": "0x" + "2" * 40,
            "fee": 500,
            "hooks": hooks,
        },
    }
    if has_liquidity is not None:
        row["hasLiquidity"] = has_liquidity
    return row


def _run_quote_lookup_cases(module) -> None:
    token_in = "0x" + "1" * 40
    token_out = "0x" + "2" * 40
    true_id = "0x" + "a" * 64
    unknown_id = "0x" + "b" * 64
    false_id = "0x" + "c" * 64
    true_pool = _quote_pool(true_id, has_liquidity=True)
    unknown_pool = _quote_pool(unknown_id, has_liquidity=None)
    false_pool = _quote_pool(false_id, has_liquidity=False)

    def lookup(rows: list[dict]) -> dict:
        original = module._pool_rows
        module._pool_rows = lambda network=None, chain_id=None: rows
        try:
            return module._pool_lookup_by_tokens(token_in, token_out)
        finally:
            module._pool_rows = original

    assert lookup([false_pool, unknown_pool, true_pool])["poolId"] == true_id
    assert lookup([false_pool, unknown_pool])["poolId"] == unknown_id
    assert lookup([unknown_pool, false_pool])["poolId"] == unknown_id
    assert lookup([false_pool])["poolId"] == false_id

    # Known true beats a zero-hook pool whose liquidity is explicitly false.
    hooked_true = _quote_pool(true_id, has_liquidity=True, hooks="0x" + "3" * 40)
    assert lookup([false_pool, hooked_true])["poolId"] == true_id
    # Unknown still outranks explicit false even when false has zero hooks.
    assert lookup([false_pool, unknown_pool])["poolId"] == unknown_id


def test_mainnet_quote_adapter_keeps_unknown_pools_before_explicit_false():
    _run_quote_lookup_cases(mainnet_quote_adapter)


def test_testnet_quote_adapter_keeps_unknown_pools_before_explicit_false():
    _run_quote_lookup_cases(testnet_quote_adapter)


def test_flash_loan_pool_view_keeps_unknown_liquidity_none():
    pool_id = "0x" + "cc" * 32
    pool_row = {
        "pool_id": pool_id,
        "token0": ETH,
        "token1": TOKEN,
        "fee": 3000,
        "hooks": ZERO_HOOKS,
        "pool_key_json": json.dumps(
            {
                "currency0": ETH,
                "currency1": TOKEN,
                "fee": 3000,
                "hooks": ZERO_HOOKS,
            }
        ),
    }
    liquidity_row = {
        "pool_id": pool_id,
        "has_liquidity": None,
        "liquidity": "",
        "liquidity_index": None,
    }
    rows = flash_loan_pool_views._robinhood_v4_pool_rows_with_liquidity(
        [pool_row],
        [liquidity_row],
    )
    assert rows[0]["hasLiquidity"] is None

    confirmed = dict(liquidity_row)
    confirmed["has_liquidity"] = 1
    confirmed["liquidity"] = "1000000000000000000000"
    confirmed["liquidity_index"] = 1000.0
    rows = flash_loan_pool_views._robinhood_v4_pool_rows_with_liquidity(
        [pool_row],
        [confirmed],
    )
    assert rows[0]["hasLiquidity"] == 1


def test_testnet_candidate_row_keeps_unknown_depth_none():
    pool_id = "0x" + "dd" * 32
    row = testnet_candidates._pool_candidate_row(
        {
            "poolId": pool_id,
            "token0": ETH,
            "token1": TOKEN,
            "token0Symbol": "ETH",
            "token1Symbol": "TOKEN",
            "fee": 3000,
            "hooks": ZERO_HOOKS,
            "source": "robinhood_testnet_v4_pool_index",
        },
        pool_manager=ETH,
        pool_liquidity_by_id={pool_id: {"hasLiquidity": None}},
    )
    assert row is not None
    assert row["hasLiquidity"] is None
    assert row["usableForObservation"] is None

    row = testnet_candidates._pool_candidate_row(
        {
            "poolId": pool_id,
            "token0": ETH,
            "token1": TOKEN,
            "token0Symbol": "ETH",
            "token1Symbol": "TOKEN",
            "fee": 3000,
            "hooks": ZERO_HOOKS,
            "source": "robinhood_testnet_v4_pool_index",
        },
        pool_manager=ETH,
        pool_liquidity_by_id={pool_id: {"hasLiquidity": False, "status": "no_liquidity"}},
    )
    assert row is not None
    assert row["hasLiquidity"] is False
    assert row["usableForObservation"] is False


def test_quote_surface_builder_keeps_unknown_pool_state():
    assert _pool_usable_state({"hasLiquidity": True}) is True
    assert _pool_usable_state({"hasLiquidity": False}) is False
    assert _pool_usable_state({}) is None
    assert _pool_usable_state({"liquidity": "0"}) is False
    assert _pool_usable_state({"liquidityIndex": 10.0}) is True
