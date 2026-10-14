import pytest
from flask import Flask

from market.binance_market import quote_service
from market.binance_market import pool_candidates
from market.binance_market import dex_surfaces
from market.binance_market import pool_candidate_filtering
from market.binance_market.pool_candidate_filtering import _candidate_pool_source_available
from market.binance_market.candidate_pool_cache import store as candidate_pool_cache_store
from dex.intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups
from dex.intent_trade.chains.robinhood_testnet import market_observation as robinhood_testnet_market_observation
from dex.intent_trade.chains.robinhood import dex_pool_candidates as robinhood_dex_pool_candidates
from core.chain_data_registry import chain_data_by_chain_id
from market.strategy.arbitrage import ArbitrageConfig
from market.binance_market.dex_surface_v4 import _static_depth_available
from web.data_routes import dex_surface_response
from core.db import builders
from core.db import page_payloads
from core.db.builders import _fixed_fee_no_hook_sql
from core.db import import_existing_json
from core.dex_candidate_response import compact_candidate_payload
from core.db.storage_dex_candidate import MAX_USABLE_CANDIDATE_FIXED_FEE, _candidate_fixed_fee_too_high
from dex.intent_trade.chains.robinhood import market_observation as robinhood_market_observation


def _resolve_testnet_database_path():
    import os
    import sqlite3
    from pathlib import Path

    candidates = []
    env_testnet = os.getenv("FLASHLOAN_TESTNET_KV_PATH", "").strip()
    if env_testnet:
        candidates.append(Path(env_testnet))
    candidates.append(Path(__file__).resolve().parents[3] / "tmp_testnet.sqlite3")
    env_generic = os.getenv("FLASHLOAN_KV_PATH", "").strip()
    if env_generic:
        candidates.append(Path(env_generic))
    for candidate in candidates:
        path = Path(candidate).expanduser().resolve()
        if not path.is_file():
            continue
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM chain_tokens "
                    "WHERE network = 'robinhood-testnet' AND chain_id = 46630"
                ).fetchone()[0]
            finally:
                connection.close()
            if int(count or 0) > 0:
                return path
        except (OSError, sqlite3.Error):
            continue
    return None


def _write_json(path, payload):
    import json

    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated_dex_surface_cache():
    old_cache = dict(dex_surfaces._DEX_SURFACE_CACHE)
    dex_surfaces._DEX_SURFACE_CACHE.clear()
    try:
        yield
    finally:
        dex_surfaces._DEX_SURFACE_CACHE.clear()
        dex_surfaces._DEX_SURFACE_CACHE.update(old_cache)


def _pool(pool_id: str, token0_symbol: str, token1_symbol: str, token0: str, token1: str, *, price_native: str, token0_amount: float, token1_amount: float, tvl_usd: float) -> dict:
    return {
        "pool": pool_id,
        "dex": "uniswap",
        "protocol": "uniswap_v3",
        "fee": "3000",
        "token0": token0,
        "token1": token1,
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "balances": {"token0": token0_amount, "token1": token1_amount, "tvlUsd": tvl_usd},
        "market": {"priceNative": price_native, "priceUsd": "1"},
        "depthBand": {"level": "deep", "label": "deep"},
    }


def test_robinhood_testnet_candidate_pagination_defaults_to_20_rows(monkeypatch):
    app = Flask(__name__)
    with app.test_request_context("/api/dex-pools/candidates?chain_id=46630"):
        page, page_size, query, quality = dex_surface_response.candidate_pagination_args()
        assert page == 1
        assert page_size == 20
        assert query == ""
        assert quality == "all"


def test_candidate_fixed_fee_above_3000_is_skipped_at_storage_stage():
    assert _candidate_fixed_fee_too_high(3001) is True
    assert _candidate_fixed_fee_too_high(10000) is True
    assert _candidate_fixed_fee_too_high(3000) is False
    assert _candidate_fixed_fee_too_high(2500) is False
    assert _candidate_fixed_fee_too_high(100001) is True
    assert _candidate_fixed_fee_too_high(8388608) is True


def test_candidate_builder_sql_caps_fixed_fee_at_3000():
    candidate_sql = _fixed_fee_no_hook_sql(max_fixed_fee=MAX_USABLE_CANDIDATE_FIXED_FEE)
    observation_sql = _fixed_fee_no_hook_sql(max_fixed_fee=MAX_USABLE_CANDIDATE_FIXED_FEE)
    assert "p.fee <= 3000" in candidate_sql
    assert "p.fee <= 100000" not in candidate_sql
    assert "p.fee <= 3000" in observation_sql
    assert "p.fee <= 100000" not in observation_sql


def test_candidate_representative_pool_rows_bounds_each_pair_to_six():
    rows = [
        {
            "pool_id": f"0x{index:02x}",
            "liquidity_index": 100 - index,
            "fee": fee,
            "price": price,
        }
        for index, (fee, price) in enumerate(
            (
                (3000, 100),
                (500, 50),
                (1000, 10),
                (2500, 90),
                (200, 80),
                (100, 70),
                (300, 60),
                (150, 200),
            ),
            start=1,
        )
    ]

    selected = builders._representative_pool_rows(rows, limit=6)

    assert len(selected) == 6
    assert selected[0]["pool_id"] == "0x01"
    selected_ids = {row["pool_id"] for row in selected}
    assert "0x01" in selected_ids
    assert selected_ids == {"0x01", "0x02", "0x03", "0x04", "0x05", "0x06"}
    assert "0x03" in selected_ids


def test_robinhood_v4_candidate_row_marks_dynamic_special_fee_and_liquidity():
    dynamic_row = pool_candidates._robinhood_pool_candidate_row(
        {
            "pool": "0x8274dc191747b879892ab245e7ed45e829b9244",
            "token0": "0x0000000000000000000000000000000000000000",
            "token1": "0x5259fc...ce1e18",
            "token0Symbol": "ETH",
            "token1Symbol": "AIGYR",
            "protocol": "uniswap_v4",
            "fee": "8388608",
            "hasLiquidity": False,
            "liquidityIndex": 0,
        }
    )
    assert dynamic_row["dynamicFee"] is True
    assert dynamic_row["specialFee"] is True
    assert dynamic_row["feeLabel"] == "dynamic"
    assert dynamic_row["usableForObservation"] is False
    assert dynamic_row["quality"] == "rejected"

    special_row = pool_candidates._robinhood_pool_candidate_row(
        {
            "pool": "0xabc",
            "token0": "0x0000000000000000000000000000000000000000",
            "token1": "0xdef",
            "token0Symbol": "ETH",
            "token1Symbol": "X",
            "protocol": "uniswap_v4",
            "fee": "899900",
            "hasLiquidity": False,
            "liquidityIndex": 0,
        }
    )
    assert special_row["dynamicFee"] is False
    assert special_row["specialFee"] is True
    assert special_row["feeLabel"] == "special"
    assert special_row["usableForObservation"] is False
    assert special_row["quality"] == "rejected"

    liquid_row = pool_candidates._robinhood_pool_candidate_row(
        {
            "pool": "0xdef",
            "token0": "0x0000000000000000000000000000000000000000",
            "token1": "0x123",
            "token0Symbol": "ETH",
            "token1Symbol": "Y",
            "protocol": "uniswap_v4",
            "fee": "3000",
            "hasLiquidity": True,
            "liquidityIndex": 12345,
        }
    )
    assert liquid_row["specialFee"] is False
    assert liquid_row["feeLabel"] == "3000"
    assert liquid_row["usableForObservation"] is True
    assert liquid_row["depthBand"]["level"] == "liquidity-index"
    assert liquid_row["quality"] == "pass"


def test_page_candidate_pool_row_enforces_fixed_fee_upper_bound():
    def row(fee, usable=None):
        return page_payloads._candidate_pool_row(
            {
                "token0": "0x" + "1" * 40,
                "token1": "0x" + "2" * 40,
                "fee": fee,
                "hooks": "0x0000000000000000000000000000000000000000",
                "usable_for_observation": usable,
            },
            token_symbols={},
            anchor_address="0x" + "1" * 40,
            anchor_symbol="ETH",
        )

    assert row(3000)["quality"] == "pass"
    assert row(3001)["quality"] == "rejected"
    assert row(10861)["quality"] == "rejected"
    assert row(10861, usable=False)["quality"] == "rejected"
    assert row(3000, usable=True)["quality"] == "pass"
    assert row(3000, usable=False)["quality"] == "rejected"


def test_non_testnet_candidate_pagination_keeps_legacy_default(monkeypatch):
    app = Flask(__name__)
    with app.test_request_context("/api/dex-pools/candidates?chain_id=4663"):
        _, page_size, _, _ = dex_surface_response.candidate_pagination_args()
        assert page_size == 20


def test_candidate_pagination_allows_larger_page_size_when_requested(monkeypatch):
    app = Flask(__name__)
    with app.test_request_context("/api/dex-pools/candidates?chain_id=46630&page_size=1000"):
        _, page_size, _, _ = dex_surface_response.candidate_pagination_args()
        assert page_size == 1000


def test_candidate_payload_paginates_pools_inside_anchor_groups():
    payload = {
        "anchorGroups": [
            {
                "anchorSymbol": "ETH",
                "ycZcSameSet": True,
                "ycPoolsRef": "sharedPools",
                "zcPoolsRef": "sharedPools",
                "sharedTokens": [],
                "sharedPools": [
                    {"pool": "pool-1", "fee": 500},
                    {"pool": "pool-2", "fee": 1000},
                    {"pool": "pool-3", "fee": 3000},
                ],
            }
        ],
        "pairs": [],
        "summary": {"anchorGroupCount": "1"},
    }
    result = compact_candidate_payload(payload, page=2, page_size=1)
    group = result["anchorGroups"][0]
    assert len(group["sharedPools"]) == 1
    assert group["sharedPools"][0]["pool"] == "pool-2"
    assert group["poolPagination"]["page"] == 2
    assert group["poolPagination"]["total_count"] == 3
    assert result["pagination"]["candidate_groups"]["unit"] == "pool"
    assert result["pagination"]["candidate_groups"]["total_count"] == 3


def test_candidate_payload_paginates_abnormal_tables_independently():
    payload = {
        "anchorGroups": [
            {
                "anchorSymbol": "ETH",
                "ycZcSameSet": True,
                "ycPoolsRef": "sharedPools",
                "zcPoolsRef": "sharedPools",
                "sharedTokens": [],
                "sharedPools": [],
                "abnormalTokens": [
                    {"symbol": f"TOKEN-{index}", "address": f"0x{index:040x}", "poolCount": 1}
                    for index in range(1, 25)
                ],
                "abnormalPools": [
                    {"pool": f"abnormal-{index}", "fee": 5000, "dynamicFee": False}
                    for index in range(1, 45)
                ],
            }
        ],
        "pairs": [],
        "summary": {"anchorGroupCount": "1"},
    }
    result = compact_candidate_payload(
        payload,
        page=1,
        page_size=20,
        abnormal_pool_page=2,
        abnormal_token_page=2,
    )
    group = result["anchorGroups"][0]
    assert len(group["abnormalTokens"]) == 4
    assert group["abnormalTokens"][0]["symbol"] == "TOKEN-21"
    assert group["abnormalTokenPagination"]["page"] == 2
    assert group["abnormalTokenPagination"]["total_count"] == 24
    assert len(group["abnormalPools"]) == 20
    assert group["abnormalPools"][0]["pool"] == "abnormal-21"
    assert group["abnormalPoolPagination"]["page"] == 2
    assert group["abnormalPoolPagination"]["total_count"] == 44


def test_filter_anchor_group_protocols_filters_abnormal_tables():
    group = {
        "anchorSymbol": "ETH",
        "ycZcSameSet": True,
        "sharedPools": [
            {
                "pool": "normal-v4",
                "protocol": "uniswap_v4",
                "counterAddress": "0x" + "1" * 40,
                "counterSymbol": "A",
            }
        ],
        "sharedTokens": [],
        "abnormalPools": [
            {
                "pool": "abnormal-v4",
                "protocol": "uniswap_v4",
                "counterAddress": "0x" + "2" * 40,
                "counterSymbol": "B",
            },
            {
                "pool": "abnormal-v3",
                "protocol": "uniswap_v3",
                "counterAddress": "0x" + "3" * 40,
                "counterSymbol": "C",
            },
        ],
        "abnormalTokens": [],
        "abnormalPoolCount": "2",
        "abnormalTokenCount": "0",
        "abnormalDirectionCount": "4",
    }

    filtered = pool_candidate_filtering._filter_anchor_group_protocols(group, {"uniswap_v4"})

    assert [row["pool"] for row in filtered["abnormalPools"]] == ["abnormal-v4"]
    assert {token["symbol"] for token in filtered["abnormalTokens"]} == {"B"}
    assert filtered["abnormalPoolCount"] == "1"
    assert filtered["abnormalTokenCount"] == "1"
    assert filtered["abnormalDirectionCount"] == "2"


def test_filter_payload_protocols_updates_abnormal_summary_counts():
    payload = {
        "summary": {},
        "pairs": [],
        "anchorGroups": [
            {
                "anchorSymbol": "ETH",
                "ycZcSameSet": True,
                "sharedPools": [],
                "sharedTokens": [],
                "sharedPoolCount": "0",
                "ycPoolCount": "0",
                "zcPoolCount": "0",
                "ycTokenCount": "0",
                "zcTokenCount": "0",
                "abnormalPools": [
                    {
                        "pool": "abnormal-v4",
                        "protocol": "uniswap_v4",
                        "counterAddress": "0x" + "1" * 40,
                        "counterSymbol": "B",
                    }
                ],
                "abnormalTokens": [],
                "abnormalPoolCount": "1",
                "abnormalTokenCount": "0",
            }
        ],
    }

    result = pool_candidate_filtering._filter_payload_protocols(payload, {"uniswap_v4"})

    assert len(result["anchorGroups"]) == 1
    assert result["summary"]["abnormalStaticPoolCount"] == "1"
    assert result["summary"]["abnormalStaticTokenCount"] == "1"


def test_mark_v4_shared_yc_zc_sets_separates_normal_and_abnormal_pools():
    zero = "0x0000000000000000000000000000000000000000"
    token_a = "0x" + "1" * 40
    token_b = "0x" + "2" * 40
    normal = {
        "pool": "0x" + "a1" * 32,
        "protocol": "uniswap_v4",
        "dex": "uniswap_v4",
        "anchorAddress": zero,
        "anchorSymbol": "ETH",
        "counterAddress": token_a,
        "counterSymbol": "TOKEN-A",
        "fee": 3000,
        "hooks": zero,
        "token0": zero,
        "token1": token_a,
    }
    abnormal = {
        **normal,
        "pool": "0x" + "b2" * 32,
        "counterAddress": token_b,
        "counterSymbol": "TOKEN-B",
        "fee": 5000,
    }

    groups = robinhood_dex_pool_candidates._mark_v4_shared_yc_zc_sets(
        [
            {
                "anchorSymbol": "ETH",
                "ycPools": [normal, abnormal],
                "zcPools": [normal, abnormal],
            }
        ]
    )

    group = groups[0]
    assert [row["pool"] for row in group["sharedPools"]] == ["0x" + "a1" * 32]
    assert [row["pool"] for row in group["abnormalPools"]] == ["0x" + "b2" * 32]
    assert group["sharedPoolCount"] == "1"
    assert group["abnormalPoolCount"] == "1"
    assert {token["symbol"] for token in group["sharedTokens"]} == {"TOKEN-A"}
    assert {token["symbol"] for token in group["abnormalTokens"]} == {"TOKEN-B"}


def test_market_observation_pool_usable_is_static_liquidity_stays_overview_gate():
    pool = {
        "fee": 3000,
        "hooks": "0x0000000000000000000000000000000000000000",
        "hasLiquidity": False,
        "liquidityIndex": 0,
    }
    assert robinhood_market_observation._observation_pool_usable(pool) is True
    assert robinhood_market_observation._pool_usable_for_observation(pool) is True
    assert robinhood_market_observation._pool_liquidity_qualified(pool) is False
    assert robinhood_market_observation._observation_pool_usable({**pool, "fee": 3001}) is False


def test_import_existing_json_filters_observation_pools_by_fixed_fee_no_hook():
    allowed = {"fee": 3000, "hooks": "0x0000000000000000000000000000000000000000"}
    assert import_existing_json._observation_pool_allowed(allowed) is True
    assert import_existing_json._observation_pool_allowed({**allowed, "fee": 3001}) is False
    assert import_existing_json._observation_pool_allowed({**allowed, "fee": 100001}) is False
    assert import_existing_json._observation_pool_allowed({**allowed, "fee": 8388608}) is False
    assert import_existing_json._observation_pool_allowed({**allowed, "hooks": "0x1"}) is False


def test_mainnet_anchor_address_resolution_uses_exact_five_addresses():
    assert import_existing_json._anchor_address_from_symbol("ETH") == "0x0000000000000000000000000000000000000000"
    assert import_existing_json._anchor_address_from_symbol("WETH") == "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
    assert import_existing_json._anchor_address_from_symbol("USDG") == "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
    assert import_existing_json._anchor_address_from_symbol("USDE") == "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
    assert import_existing_json._anchor_address_from_symbol("TZ") == "0xa8cf7c51eee671646ba440ffa180f970902fd2fb"
    assert import_existing_json._anchor_address_from_symbol("usdg") == "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
    assert import_existing_json._anchor_address_from_symbol("NOT-CONFIGURED") == ""
    assert builders._resolve_anchor_address(
        "USDG",
        [{"token_symbol": "USDG", "token_address": "0x0000000000000000000000000000000000000000"}],
        [],
    ) == "0x5fc5360d0400a0fd4f2af552add042d716f1d168"


def test_build_loops_requires_all_three_hops_to_have_pools():
    anchor = "0x" + "a" * 40
    y = "0x" + "b" * 40
    z = "0x" + "c" * 40

    def pool(pool_id, token0, token1):
        return {
            "pool": pool_id,
            "token0": token0,
            "token1": token1,
            "liquidity_index": 1.0,
        }

    candidates = {"tokens": [{"token_address": y}, {"token_address": z}]}
    complete = {
        "pools": [
            pool("yc", anchor, y),
            pool("yz", y, z),
            pool("zc", z, anchor),
        ]
    }

    loops, hops = import_existing_json._build_loops(
        anchor_symbol="ETH",
        anchor_address=anchor,
        candidates=candidates,
        observation=complete,
    )

    assert len(loops) == 2
    assert len(hops) == 6
    assert {tuple(loop["pool_ids"]) for loop in loops} == {
        ("yc", "yz", "zc"),
        ("zc", "yz", "yc"),
    }
    assert {hop["hop_index"] for hop in hops} == {1, 2, 3}

    missing_yz = {"pools": [pool("yc", anchor, y), pool("zc", z, anchor)]}
    loops, hops = import_existing_json._build_loops(
        anchor_symbol="ETH",
        anchor_address=anchor,
        candidates=candidates,
        observation=missing_yz,
    )
    assert loops == []
    assert hops == []


def test_mainnet_yz_pair_keeps_every_fixed_fee_pool_for_market_observation():
    token_a = "0x" + "0" * 40
    token_b = "0x" + "1" * 40
    anchor = "0x" + "2" * 40
    pools_by_edge = {
        (token_a, token_b): [
            {
                "pool": f"0xpool-{index}",
                "token0": token_a,
                "token1": token_b,
                "token0Symbol": "A",
                "token1Symbol": "B",
                "fee": fee,
                "hooks": "0x0000000000000000000000000000000000000000",
            }
            for index, fee in enumerate((500, 1000, 2000, 2500, 3000, 30, 100), start=1)
        ]
    }
    rows = robinhood_market_observation._mainnet_yz_pairs_from_tokens(
        pools_by_edge,
        [{"symbol": "A", "address": token_a}, {"symbol": "B", "address": token_b}],
        include_directions=False,
        anchor_symbol="ETH",
        anchor_address=anchor,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["anchorSymbol"] == "ETH"
    assert row["anchorAddress"] == anchor
    assert row["poolCount"] == 7
    assert len(row["executionPools"]) == 7
    assert len(row["preferredPools"]) == 7
    assert len(row["strictQualityPools"]) == 7


def test_mainnet_associated_observation_rows_attach_anchor_to_y_and_z():
    token_a = "0x" + "0" * 40
    token_b = "0x" + "1" * 40
    anchor = "0x" + "2" * 40
    pool = {
        "pool": "0xpool-anchor-associated",
        "token0": token_a,
        "token1": token_b,
        "token0Symbol": "A",
        "token1Symbol": "B",
        "fee": 500,
        "hooks": "0x0000000000000000000000000000000000000000",
    }
    pools_by_edge = {(token_a, token_b): [pool]}
    bridge_pairs = robinhood_market_observation._mainnet_yz_pairs_from_tokens(
        pools_by_edge,
        [{"symbol": "A", "address": token_a}, {"symbol": "B", "address": token_b}],
        include_directions=True,
        anchor_symbol="ETH",
        anchor_address=anchor,
    )
    pair_rows, pool_rows, _ = robinhood_market_observation._mainnet_associated_observation_rows(
        bridge_pairs,
        [pool],
    )
    assert len(pair_rows) == 2
    for row in pair_rows:
        assert row["anchorSymbol"] == "ETH"
        assert row["associatedFundSymbols"] == ["ETH"]
        assert row["token0"]["associatedFunds"] == ["ETH"]
        assert row["token1"]["associatedFunds"] == ["ETH"]
        assert row["directSwapPools"][0]["anchorSymbol"] == "ETH"
    assert pool_rows[0]["associatedFundSymbols"] == ["ETH"]


def test_mainnet_associated_observation_rows_attach_non_normal_pools():
    token_a = "0x" + "0" * 40
    token_b = "0x" + "1" * 40
    anchor = "0x" + "2" * 40
    bridge_pairs = robinhood_market_observation._mainnet_yz_pairs_from_tokens(
        {},
        [{"symbol": "A", "address": token_a}, {"symbol": "B", "address": token_b}],
        include_directions=True,
        anchor_symbol="ETH",
        anchor_address=anchor,
    )
    non_normal_pools = [
        {
            "pool": f"0xpool-{index}",
            "token0": token_a,
            "token1": token_b,
            "token0Symbol": "A",
            "token1Symbol": "B",
            "fee": fee,
            "hooks": hooks,
        }
        for index, (fee, hooks) in enumerate(
            (
                (3000, "0x" + "1" * 40),
                (3001, "0x0000000000000000000000000000000000000000"),
                (8388608, "0x0000000000000000000000000000000000000000"),
            ),
            start=1,
        )
    ]
    pair_rows, pool_rows, _ = robinhood_market_observation._mainnet_associated_observation_rows(
        bridge_pairs,
        [],
        non_normal_pools=non_normal_pools,
    )
    assert len(pair_rows) == 2
    assert pool_rows == []
    for row in pair_rows:
        assert row["hasDirectSwapPool"] is False
        assert row["hasNonNormalSwapPool"] is True
        assert row["nonNormalSwapPoolCount"] == 3
        assert len(row["nonNormalSwapPools"]) == 3
        assert row["observationEmptyReason"] == "仅存在非正常 Y-Z 交易池"


def test_robinhood_mainnet_static_quote_routes_keep_protocol_scope():
    assert dex_surfaces._robinhood_static_scope_protocols(
        chain_id=4663,
        protocols=["uniswap_v3", "uniswap_v4"],
        use_static_observation=True,
    ) == ["uniswap_v3", "uniswap_v4"]
    assert dex_surfaces._robinhood_static_scope_protocols(
        chain_id=4663,
        protocols=["uniswap_v3"],
        use_static_observation=True,
    ) == ["uniswap_v3"]


def test_robinhood_testnet_static_quote_routes_keep_protocol_scope():
    assert dex_surfaces._robinhood_static_scope_protocols(
        chain_id=46630,
        protocols=["uniswap_v3", "uniswap_v4"],
        use_static_observation=True,
    ) == ["uniswap_v3", "uniswap_v4"]


def test_static_route_liquidity_evidence_distinguishes_explicit_and_unknown():
    from web.data_routes.dex_surface_routes_quotes import _static_route_liquidity_evidence

    liquid_pool = {
        "pool": "0x" + "a" * 64,
        "poolKey": {"currency0": "0x0", "currency1": "0xaa", "fee": 500, "tickSpacing": 10, "hooks": "0x0"},
        "hasLiquidity": True,
        "usableForObservation": True,
        "liquidityIndex": 100.0,
    }
    no_evidence_pool = {
        "pool": "0x" + "b" * 64,
        "poolKey": {"currency0": "0x0", "currency1": "0xbb", "fee": 500, "tickSpacing": 10, "hooks": "0x0"},
    }
    explicit_unusable_pool = {
        "pool": "0x" + "c" * 64,
        "poolKey": {"currency0": "0x0", "currency1": "0xcc", "fee": 500, "tickSpacing": 10, "hooks": "0x0"},
        "hasLiquidity": False,
        "usableForObservation": False,
    }

    usable, explicit = _static_route_liquidity_evidence([liquid_pool])
    assert usable is True and explicit is True
    usable, explicit = _static_route_liquidity_evidence([no_evidence_pool])
    assert usable is None and explicit is False
    usable, explicit = _static_route_liquidity_evidence([explicit_unusable_pool])
    assert usable is False and explicit is True
    usable, explicit = _static_route_liquidity_evidence([liquid_pool, no_evidence_pool])
    assert usable is True and explicit is True
    usable, explicit = _static_route_liquidity_evidence([liquid_pool, explicit_unusable_pool])
    assert usable is False and explicit is True


def test_static_quote_routes_keeps_unlabelled_closed_loops_and_exposes_liquid_registry(monkeypatch):
    from web.data_routes.dex_surface_routes_quotes import register_dex_quote_routes
    from web.data_routes import dex_surfaces as surface_root

    def _pool(pool_id: str, symbols: tuple[str, str], *, usable=None, has_liquidity=None):
        return {
            "pool": pool_id,
            "poolId": pool_id,
            "poolKey": {
                "currency0": "0x" + "1" * 40,
                "currency1": "0x" + "2" * 40,
                "fee": 500,
                "tickSpacing": 10,
                "hooks": "0x0000000000000000000000000000000000000000",
            },
            "token0Symbol": symbols[0],
            "token1Symbol": symbols[1],
            "pairSymbol": f"{symbols[0]}:{symbols[1]}",
            "symbolPair": f"{symbols[0]}:{symbols[1]}",
            "routeSymbols": list(symbols),
            "routeAddresses": ["0x" + "1" * 40, "0x" + "2" * 40],
            "fee": 500,
            "usableForObservation": usable,
            "hasLiquidity": has_liquidity,
        }

    overview = {
        "exists": True,
        "path": "overview.json",
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "summary": {
            "liquidPoolCount": 1,
            "closedLoopRouteCount": 2,
        },
        "closedLoopRouteSummaries": [
            {
                "anchorSymbol": "ETH",
                "counterSymbol": "AAA",
                "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                "pathText": "ETH -> AAA -> BBB -> ETH",
                "routeAddresses": ["0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40, "0x" + "1" * 40],
                "routePools": [
                    _pool("0x" + "a" * 64, ("ETH", "AAA"), usable=None, has_liquidity=None),
                    _pool("0x" + "b" * 64, ("AAA", "BBB"), usable=None, has_liquidity=None),
                    _pool("0x" + "c" * 64, ("BBB", "ETH"), usable=None, has_liquidity=None),
                ],
            },
            {
                "anchorSymbol": "ETH",
                "counterSymbol": "CCC",
                "pathSymbols": ["ETH", "CCC", "DDD", "ETH"],
                "pathText": "ETH -> CCC -> DDD -> ETH",
                "routeAddresses": ["0x" + "1" * 40, "0x" + "4" * 40, "0x" + "5" * 40, "0x" + "1" * 40],
                "routePools": [
                    _pool("0x" + "d" * 64, ("ETH", "CCC"), usable=False, has_liquidity=False),
                    _pool("0x" + "e" * 64, ("CCC", "DDD"), usable=False, has_liquidity=False),
                    _pool("0x" + "f" * 64, ("DDD", "ETH"), usable=False, has_liquidity=False),
                ],
            },
        ],
        "poolRegistry": {
            "0x" + "a" * 64: _pool("0x" + "a" * 64, ("ETH", "AAA")),
            "0x" + "b" * 64: _pool("0x" + "b" * 64, ("AAA", "BBB")),
            "0x" + "c" * 64: _pool("0x" + "c" * 64, ("BBB", "ETH")),
            "0x" + "d" * 64: _pool("0x" + "d" * 64, ("ETH", "CCC"), usable=False, has_liquidity=False),
            "0x" + "e" * 64: _pool("0x" + "e" * 64, ("CCC", "DDD"), usable=False, has_liquidity=False),
            "0x" + "f" * 64: _pool("0x" + "f" * 64, ("DDD", "ETH"), usable=False, has_liquidity=False),
        },
        "liquidPoolRegistry": {
            "0x" + "g" * 64: _pool("0x" + "g" * 64, ("ETH", "GGG"), usable=True, has_liquidity=True),
        },
    }

    monkeypatch.setattr(
        surface_root,
        "load_observation_overview_for_scope",
        lambda **kwargs: overview,
    )
    monkeypatch.setattr(
        surface_root,
        "selected_chain_id_from_request",
        lambda: ({"chain_id": 46630, "market_id": "dex-arbitrage-46630-uniswap-v4"}, 46630),
    )
    monkeypatch.setattr(
        surface_root,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        surface_root,
        "_attach_dex_strategy",
        lambda payload: payload,
    )

    app = Flask(__name__)
    register_dex_quote_routes(app)
    with app.test_client() as client:
        response = client.get("/api/dex/quote-routes/static?chain_id=46630&protocols=uniswap_v4")
        payload = response.get_json()

    assert response.status_code == 200
    # The unlabelled route is preserved; the explicitly-unusable route is dropped.
    assert len(payload["routes"]) == 1
    assert payload["routes"][0]["routeUsableForObservation"] is None
    assert payload["routes"][0]["routeHasLiquidityEvidence"] is False
    assert payload["summary"]["candidateLiquidPoolCount"] == 1
    assert payload["summary"]["closedLoopLiquidPoolCount"] == 1
    assert payload["summary"]["liquidPoolRegistryCount"] == 1
    assert payload["liquidPoolRegistry"]["0x" + "g" * 64]["hasLiquidity"] is True


def test_static_quote_routes_drops_route_with_mixed_explicit_unusable_liquidity(monkeypatch):
    from web.data_routes.dex_surface_routes_quotes import _route_has_explicit_unusable_liquidity

    liquid = {
        "pool": "0x" + "a" * 64,
        "poolId": "0x" + "a" * 64,
        "hasLiquidity": True,
        "usableForObservation": True,
    }
    unusable = {
        "pool": "0x" + "b" * 64,
        "poolId": "0x" + "b" * 64,
        "hasLiquidity": False,
        "usableForObservation": False,
    }
    missing = {
        "pool": "0x" + "c" * 64,
        "poolId": "0x" + "c" * 64,
    }

    assert _route_has_explicit_unusable_liquidity({"routePools": [liquid, unusable, missing]}) is True
    assert _route_has_explicit_unusable_liquidity({"routePools": [liquid, missing]}) is False
    assert _route_has_explicit_unusable_liquidity({"routePools": []}) is True


def test_build_binance_market_state_uses_shared_pool_candidates(monkeypatch):
    monkeypatch.setattr(
        quote_service,
        "dex_pool_candidates_payload",
        lambda: {
            "mode": "stub_pool_candidates",
            "pairs": [],
            "summary": {},
            "poolStaticTable": {},
        },
    )

    payload = quote_service.build_binance_market_state(
        {},
        aave_symbols=[],
        arbitrage_config=ArbitrageConfig(notional_usd=1, trade_fee_percent=0, flashloan_fee_percent=0),
        top_limit=1,
        bottom_limit=1,
    )

    assert payload["pool_candidates"]["mode"] == "stub_pool_candidates"


def test_robinhood_mainnet_candidate_source_available_with_relative_data_file(monkeypatch, tmp_path):
    fake_path = tmp_path / "v4_pool_index.json"
    fake_path.write_text("{}", encoding="utf-8")

    def fake_chain_data_file_path(network, key, *, chain_id=None, root=None):
        assert key == "v4_pool_index"
        assert chain_id == 4663
        return fake_path

    monkeypatch.setattr(pool_candidate_filtering, "chain_data_file_path", fake_chain_data_file_path)

    assert _candidate_pool_source_available(4663) is True


def test_robinhood_mainnet_candidates_use_cache_before_source_available(monkeypatch):
    monkeypatch.setattr(
        pool_candidates,
        "_candidate_pool_source_available",
        lambda _chain_id: False,
    )

    payload = pool_candidates.dex_pool_candidates_payload(chain_id=4663, protocols=["uniswap_v4"])

    assert payload["poolStaticTable"]["exists"] is True
    assert payload["poolStaticTable"]["network"] == "robinhood"
    assert payload["poolStaticTable"]["chainId"] == 4663
    assert int(payload["summary"].get("pairTableCount") or 0) > 0
    assert int(payload["summary"].get("ycZcSameSetGroupCount") or 0) > 0
    assert payload["summary"].get("candidateSetStorageMode") == "shared_yc_zc_pool_set"
    shared_group = next(
        group for group in payload.get("anchorGroups") or []
        if group.get("ycZcSameSet")
    )
    assert shared_group["ycPoolsRef"] == "sharedPools"
    assert shared_group["zcPoolsRef"] == "sharedPools"
    assert not shared_group["ycPools"] and not shared_group["zcPools"]
    assert shared_group["sharedPools"]


def test_shared_pool_recommended_hops_only_mark_optimal_pool_ids():
    from dex.intent_trade.chains.robinhood import dex_pool_candidates as robinhood_candidates

    optimal_id = "0x" + "1" * 64
    other_id = "0x" + "2" * 64
    shared_pools = [
        {
            "pool": optimal_id,
            "counterSymbol": "TOKEN",
            "counterAddress": "0x" + "3" * 40,
            "fee": 3000,
        },
        {
            "pool": other_id,
            "counterSymbol": "OTHER",
            "counterAddress": "0x" + "4" * 40,
            "fee": 500,
        },
    ]
    shared_tokens = [
        {
            "symbol": "TOKEN",
            "address": "0x" + "3" * 40,
            "ycOptimalPoolId": optimal_id,
            "zcOptimalPoolId": optimal_id,
        }
    ]

    annotated = robinhood_candidates._annotate_shared_pool_recommended_hops(shared_pools, shared_tokens)
    by_pool = {row["pool"]: row for row in annotated}

    assert by_pool[optimal_id]["hopRecommendation"] == [
        "yc_anchor_to_counter_highest_price",
        "zc_counter_to_anchor_lowest_price",
    ]
    assert by_pool[optimal_id]["isFirstHopRecommended"] is True
    assert by_pool[optimal_id]["isThirdHopRecommended"] is True
    assert by_pool[other_id]["hopRecommendation"] == []
    assert by_pool[other_id]["isFirstHopRecommended"] is False
    assert by_pool[other_id]["isThirdHopRecommended"] is False


def test_robinhood_v4_pool_manager_address_is_same_on_mainnet_and_testnet():
    mainnet = chain_data_by_chain_id(4663) or {}
    testnet = chain_data_by_chain_id(46630) or {}
    mainnet_contracts = mainnet.get("contracts") or {}
    testnet_contracts = testnet.get("contracts") or {}

    for key in ("v4_pool_manager", "v4_quoter", "position_manager", "permit2", "universal_router"):
        assert str(mainnet_contracts.get(key) or "").lower()
        assert str(mainnet_contracts.get(key) or "").lower() == str(testnet_contracts.get(key) or "").lower(), key
    assert str(mainnet_contracts.get("v4_pool_manager") or "").lower() == "0x8366a39cc670b4001a1121b8f6a443a643e40951"


def test_anchor_candidate_groups_split_yc_and_zc_by_anchor():
    pair_reports = [
        {
            "symbolPair": "USDT:ETH",
            "tokenXSymbol": "USDT",
            "tokenYSymbol": "ETH",
            "classification": {"stableSymbols": ["USDT"], "nonStableSymbols": ["ETH"]},
            "preferredPools": [
                {
                    "pool": "0x" + "1" * 40,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "fee": "0",
                    "quality": "pass",
                    "token0": "0x" + "a" * 40,
                    "token1": "0x" + "b" * 40,
                    "token0Symbol": "USDT",
                    "token1Symbol": "ETH",
                    "balances": {"tvlUsd": 1000},
                    "depthBand": {"level": "deep", "label": "deep"},
                }
            ],
            "strictQualityPools": [],
            "executionPools": [],
        }
    ]

    groups = build_anchor_candidate_groups(pair_reports)

    assert groups[0]["anchorSymbol"] == "USDT"
    assert groups[0]["ycPoolCount"] == "1"
    assert groups[0]["zcPoolCount"] == "1"
    assert groups[0]["ycTokens"][0]["symbol"] == "ETH"
    assert groups[0]["zcTokens"][0]["symbol"] == "ETH"
    assert groups[0]["ycPools"][0]["routeText"] == "USDT -> ETH"
    assert groups[0]["zcPools"][0]["routeText"] == "ETH -> USDT"


def test_anchor_candidate_groups_include_all_symbols_when_mixed_pairs_are_present():
    pair_reports = [
        {
            "symbolPair": "USDC:FRAX",
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "FRAX",
            "classification": {"stableSymbols": ["USDC", "FRAX"], "nonStableSymbols": []},
            "preferredPools": [
                {
                    "pool": "0x" + "1" * 40,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "fee": "0",
                    "quality": "pass",
                    "token0": "0x" + "a" * 40,
                    "token1": "0x" + "b" * 40,
                    "token0Symbol": "USDC",
                    "token1Symbol": "FRAX",
                    "balances": {"tvlUsd": 1000},
                    "depthBand": {"level": "deep", "label": "deep"},
                }
            ],
            "strictQualityPools": [],
            "executionPools": [],
        },
        {
            "symbolPair": "WETH:WAVAX",
            "tokenXSymbol": "WETH",
            "tokenYSymbol": "WAVAX",
            "classification": {"stableSymbols": [], "nonStableSymbols": ["WETH", "WAVAX"]},
            "preferredPools": [
                {
                    "pool": "0x" + "2" * 40,
                    "protocol": "uniswap_v4",
                    "dex": "uniswap_v4",
                    "fee": "0",
                    "quality": "pass",
                    "token0": "0x" + "c" * 40,
                    "token1": "0x" + "d" * 40,
                    "token0Symbol": "WETH",
                    "token1Symbol": "WAVAX",
                    "balances": {"tvlUsd": 2000},
                    "depthBand": {"level": "deep", "label": "deep"},
                }
            ],
            "strictQualityPools": [],
            "executionPools": [],
        },
    ]

    groups = build_anchor_candidate_groups(pair_reports)
    anchor_symbols = [group["anchorSymbol"] for group in groups]

    assert anchor_symbols == ["FRAX", "USDC", "WAVAX", "WETH"]
    assert {group["anchorSymbol"] for group in groups} == {"FRAX", "USDC", "WAVAX", "WETH"}


def test_anchor_candidate_groups_use_all_pools_and_keep_unique_queues():
    pool_a = {
        "pool": "0x" + "1" * 40,
        "protocol": "uniswap_v3",
        "dex": "uniswap_v3",
        "fee": "3000",
        "quality": "pass",
        "token0": "0x" + "a" * 40,
        "token1": "0x" + "b" * 40,
        "token0Symbol": "USDC",
        "token1Symbol": "ETH",
        "balances": {"tvlUsd": 1000},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    pool_b = {
        "pool": "0x" + "2" * 40,
        "protocol": "uniswap_v3",
        "dex": "uniswap_v3",
        "fee": "3000",
        "quality": "rejected",
        "token0": "0x" + "a" * 40,
        "token1": "0x" + "b" * 40,
        "token0Symbol": "USDC",
        "token1Symbol": "ETH",
        "balances": {"tvlUsd": 500},
        "depthBand": {"level": "shallow", "label": "shallow"},
    }

    pair_reports = [
        {
            "symbolPair": "USDC:ETH",
            "tokenXSymbol": "USDC",
            "tokenYSymbol": "ETH",
            "classification": {"stableSymbols": ["USDC"], "nonStableSymbols": ["ETH"]},
            "allPools": [pool_a, pool_a, pool_b],
            "preferredPools": [pool_a],
            "strictQualityPools": [pool_a],
            "executionPools": [pool_a],
            "rejectedPools": [pool_b],
        }
    ]

    groups = build_anchor_candidate_groups(pair_reports)
    group = groups[0]

    assert group["candidatePoolCount"] == "2"
    assert group["candidateQueueCount"] == "4"
    assert len(group["ycPools"]) == 2
    assert len(group["zcPools"]) == 2
    assert len({row["routeKey"] for row in group["ycPools"]}) == 2


def test_anchor_candidate_groups_allow_v4_zero_address_and_skip_non_v4_zero():
    usdt = "0x" + "a" * 40
    usdc = "0x" + "b" * 40
    zero = "0x" + "0" * 40
    v4_zero_pool = {
        "pool": "0x" + "1" * 40,
        "protocol": "uniswap_v4",
        "dex": "uniswap_v4",
        "fee": "0",
        "quality": "pass",
        "token0": usdt,
        "token1": zero,
        "token0Symbol": "USDT",
        "token1Symbol": "ETH",
        "balances": {"tvlUsd": 1000},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    v3_zero_pool = {
        "pool": "0x" + "2" * 40,
        "protocol": "uniswap_v3",
        "dex": "uniswap_v3",
        "fee": "0",
        "quality": "pass",
        "token0": usdt,
        "token1": zero,
        "token0Symbol": "USDT",
        "token1Symbol": "ETH",
        "balances": {"tvlUsd": 1000},
        "depthBand": {"level": "deep", "label": "deep"},
    }
    v4_pairs = [
        {
            "symbolPair": "USDT:ETH",
            "tokenXSymbol": "USDT",
            "tokenYSymbol": "ETH",
            "classification": {"stableSymbols": ["USDT"], "nonStableSymbols": ["ETH"]},
            "preferredPools": [v4_zero_pool],
            "strictQualityPools": [],
            "executionPools": [],
        }
    ]
    v3_pairs = [
        {
            "symbolPair": "USDT:ETH",
            "tokenXSymbol": "USDT",
            "tokenYSymbol": "ETH",
            "classification": {"stableSymbols": ["USDT"], "nonStableSymbols": ["ETH"]},
            "preferredPools": [v3_zero_pool],
            "strictQualityPools": [],
            "executionPools": [],
        }
    ]

    v4_groups = build_anchor_candidate_groups(v4_pairs)
    assert {group["anchorSymbol"] for group in v4_groups} == {"USDT", "ETH"}
    assert {token["symbol"] for group in v4_groups for token in group["ycTokens"] + group["zcTokens"]} == {"USDT", "ETH"}
    eth_group = next(group for group in v4_groups if group["anchorSymbol"] == "ETH")
    usdt_group = next(group for group in v4_groups if group["anchorSymbol"] == "USDT")
    assert all(not pool_candidate_filtering._is_zero_address(token["address"]) for token in eth_group["ycTokens"] + eth_group["zcTokens"])
    assert all(pool_candidate_filtering._is_zero_address(token["address"]) for token in usdt_group["ycTokens"] + usdt_group["zcTokens"])

    v3_groups = build_anchor_candidate_groups(v3_pairs)
    assert v3_groups == []


def test_pool_candidate_filtering_anchor_tokens_skip_zero_address():
    usdc = "0x" + "1" * 40
    rows = [
        {"pool": "0x" + "a" * 40, "counterSymbol": "ETH", "counterAddress": "0x" + "0" * 40},
        {"pool": "0x" + "b" * 40, "counterSymbol": "USDC", "counterAddress": usdc},
    ]

    tokens = pool_candidate_filtering._anchor_token_groups_from_rows(rows)

    assert [token["symbol"] for token in tokens] == ["USDC"]
    assert tokens[0]["address"] == usdc


def test_pool_candidate_filtering_anchor_tokens_keep_v4_zero_address():
    usdt = "0x" + "1" * 40
    rows = [
        {
            "pool": "0x" + "a" * 40,
            "protocol": "uniswap_v4",
            "dex": "uniswap_v4",
            "counterSymbol": "ETH",
            "counterAddress": "0x" + "0" * 40,
        },
        {"pool": "0x" + "b" * 40, "counterSymbol": "USDT", "counterAddress": usdt},
    ]

    tokens = pool_candidate_filtering._anchor_token_groups_from_rows(rows)

    assert [token["symbol"] for token in tokens] == ["ETH", "USDT"]
    eth_token = next(token for token in tokens if token["symbol"] == "ETH")
    assert pool_candidate_filtering._is_zero_address(eth_token["address"])


def test_robinhood_testnet_v4_pool_candidates_use_chain_owned_flow(monkeypatch):
    testnet_database = _resolve_testnet_database_path()
    if testnet_database is None:
        pytest.skip(
            "testnet SQLite database is not available; "
            "set FLASHLOAN_TESTNET_KV_PATH or rebuild flashloan/tmp_testnet.sqlite3"
        )
    monkeypatch.setenv("FLASHLOAN_KV_PATH", str(testnet_database))
    payload = pool_candidates.dex_pool_candidates_payload(chain_id=46630, protocols=["uniswap_v4"])

    assert payload["poolStaticTable"]["network"] == "robinhood-testnet"
    assert payload["poolStaticTable"]["chainId"] == 46630
    assert payload["poolStaticTable"]["mode"] == "sqlite_db"
    assert payload["source"] == "rust_sqlite"
    assert int(payload["summary"].get("staticPoolCount") or 0) > 0
    assert payload["poolStaticTable"]["exists"] is True
    assert payload["processing"]["handler"] == (
        "intent_trade.chains.robinhood_testnet.dex_pool_candidates:dex_pool_candidates_payload"
    )
    assert int(payload["summary"]["pairTableCount"]) > 0
    assert int(payload["summary"]["anchorGroupCount"]) > 0
    assert int(payload["summary"]["anchorGroupCount"]) <= int(payload["summary"]["tokenCount"])
    assert payload["summary"]["candidateSetStorageMode"] == "shared_yc_zc_pool_set"
    assert int(payload["summary"]["ycZcSameSetGroupCount"]) > 0
    assert int(payload["summary"]["staticPoolCount"]) > 0
    assert payload["pairs"][0]["preferredPools"][0]["poolIdentityMode"] == "v4_pool_id"
    assert payload["pairs"][0]["preferredPools"][0]["protocol"] == "uniswap_v4"
    assert payload["anchorGroups"]
    assert payload["anchorGroups"][0]["ycZcSameSet"] is True
    assert payload["anchorGroups"][0]["ycPoolsRef"] == "sharedPools"
    assert payload["anchorGroups"][0]["sharedPools"]


def test_robinhood_testnet_v4_pool_candidates_merge_disjoint_index_and_liquidity_sources():
    from dex.intent_trade.chains.robinhood_testnet.dex_pool_candidates import _candidate_source_pools

    indexed = {"ok": True, "source": "index.json", "pools": [{"poolId": "0x01"}]}
    liquidity = {
        "ok": True,
        "source": "liquidity.json",
        "pools": [{"poolId": "0x02"}],
    }

    pools, mode, source = _candidate_source_pools(indexed, liquidity)

    assert [pool["poolId"] for pool in pools] == ["0x01", "0x02"]
    assert mode == "robinhood_testnet_v4_pool_sources_merged"
    assert source == "index.json"


def test_robinhood_testnet_v4_pool_candidates_include_known_cold_pool_ids(monkeypatch):
    from dex.intent_trade.chains.robinhood_testnet import dex_pool_candidates

    active_id = "0x" + "c" * 64
    known_id = "0x" + "a" * 64
    monkeypatch.setattr(
        dex_pool_candidates,
        "_known_token_pool_ids",
        lambda: {"0xknown": [f"testnet-v4-amm-manifest.tz", known_id]},
    )
    indexed = {
        "ok": True,
        "source": "index.json",
        "pools": [
            {"poolId": active_id, "token0": "0xactive0", "token1": "0xactive1"},
            {"poolId": known_id, "token0": "0xtz", "token1": "0xta"},
        ],
    }
    liquidity = {
        "ok": True,
        "source": "liquidity.json",
        "pools": [{"poolId": active_id, "token0": "0xactive0", "token1": "0xactive1"}],
    }

    pools, mode, _ = dex_pool_candidates._candidate_source_pools(indexed, liquidity)

    assert {pool["poolId"] for pool in pools} == {active_id, known_id}
    assert mode == "robinhood_testnet_v4_pool_sources_merged"


def test_v4_static_depth_available_for_indexed_sqrt_price_pool():
    assert _static_depth_available(
        {
            "protocol": "uniswap_v4",
            "liquidity": "",
            "tvlUsd": 0,
            "sqrtPriceX96": "25054144837438405210904448839",
        }
    ) is True
    assert _static_depth_available(
        {
            "protocol": "uniswap_v4",
            "liquidity": "",
            "tvlUsd": 0,
            "sqrtPriceX96": "",
        }
    ) is False


def test_dex_pool_candidates_classifies_stable_and_non_stable_pools(tmp_path, monkeypatch):
    cache_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "bnb",
            "chainId": 56,
            "pools": [
                {
                    "pool": "0x" + "1" * 40,
                    "symbolPair": "USDC:USDT",
                    "token0Symbol": "USDC",
                    "token1Symbol": "USDT",
                    "token0Usd": 500_000,
                    "token1Usd": 500_000,
                    "tvlUsd": 1_000_000,
                    "status": "recommended",
                },
                {
                    "pool": "0x" + "2" * 40,
                    "symbolPair": "USDC:WBNB",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WBNB",
                    "token0Usd": 250_000,
                    "token1Usd": 260_000,
                    "tvlUsd": 510_000,
                    "status": "recommended",
                },
                {
                    "pool": "0x" + "3" * 40,
                    "symbolPair": "BTCB:WBNB",
                    "token0Symbol": "BTCB",
                    "token1Symbol": "WBNB",
                    "token0Usd": 100_000,
                    "token1Usd": 100_000,
                    "tvlUsd": 200_000,
                    "status": "recommended",
                },
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {
            "protocols": [{"protocol": "venus_core"}, {"protocol": "pancakeswap_v2"}],
            "removed_protocols": [],
        },
    )

    payload = pool_candidates.dex_pool_candidates_payload(cache_path)

    assert payload["summary"]["pairClassCounts"] == {
        "non_stable_pair": 1,
        "stable_pair": 1,
        "stable_to_non_stable_pair": 1,
    }
    assert payload["summary"]["stablePairCount"] == "1"
    assert payload["flashLoanLiquidity"]["available_protocols"] == ["venus_core", "pancakeswap_v2"]
    assert payload["dexSettlement"]["uniswapx"]["supported"] is True
    assert payload["dexSettlement"]["arbitrage_integration"]["can_combine_with_flashloan"] is True
    by_pair = {item["symbolPair"]: item for item in payload["pairs"]}
    assert by_pair["USDC:USDT"]["classification"]["pairClass"] == "stable_pair"
    assert by_pair["USDC:WBNB"]["classification"]["pairClass"] == "stable_to_non_stable_pair"
    assert by_pair["BTCB:WBNB"]["classification"]["pairClass"] == "non_stable_pair"
    assert by_pair["USDC:WBNB"]["flashLoanLinkage"]["direct_stable_flashloan"] is True
    assert by_pair["USDC:WBNB"]["flashLoanLinkage"]["ready_for_atomic_arbitrage"] is True


def test_dex_pool_candidates_marks_uniswapx_as_settlement_not_pool(tmp_path, monkeypatch):
    cache_path = tmp_path / "polygon_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "polygon",
            "chainId": 137,
            "pairs": [
                {
                    "symbolPair": "USDC:WETH",
                    "tokenXSymbol": "USDC",
                    "tokenYSymbol": "WETH",
                    "allPools": [
                        {
                            "pool": "0x" + "4" * 40,
                            "token0_symbol": "USDC",
                            "token1_symbol": "WETH",
                            "tvlUsd": 100_000,
                            "quality": "pass",
                        }
                    ],
                    "preferredPools": [
                        {
                            "pool": "0x" + "4" * 40,
                            "token0_symbol": "USDC",
                            "token1_symbol": "WETH",
                            "tvlUsd": 100_000,
                            "quality": "pass",
                        }
                    ],
                    "executionPools": [],
                }
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": [{"protocol": "aave_v3"}]},
    )

    payload = pool_candidates.dex_pool_candidates_payload(cache_path)

    assert payload["dexSettlement"]["uniswapx"]["pool_model"] == "intent_order_flow_not_amm_pool"
    assert payload["dexSettlement"]["uniswapx"]["supported"] is False
    assert payload["dexSettlement"]["arbitrage_integration"]["can_combine_with_flashloan"] is False
    assert payload["pairs"][0]["settlementRails"] == []
    assert payload["pairs"][0]["flashLoanLinkage"]["ready_for_atomic_arbitrage"] is False


def test_dex_quote_routes_are_driven_by_candidate_pools_not_flashloan(tmp_path, monkeypatch):
    cache_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "bnb",
            "chainId": 56,
            "pools": [
                {
                    "pool": "0x" + "5" * 40,
                    "symbolPair": "USDC:WBNB",
                    "dex": "pancakeswap_v2",
                    "protocol": "v2",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WBNB",
                    "token0Usd": 600_000,
                    "token1Usd": 610_000,
                    "tvlUsd": 1_210_000,
                    "status": "recommended",
                }
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )
    monkeypatch.setattr(pool_candidates, "_candidate_pool_source_available", lambda chain_id: True)
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: pool_candidates.dex_pool_candidates_payload(cache_path))

    candidates = pool_candidates.dex_pool_candidates_payload(cache_path)
    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=56)
    request = dex_surfaces.dex_quote_request_payload(chain_id=56, pair="USDC -> WBNB -> USDC", venue="uniswapx", amount="1000")

    assert candidates["pairs"][0]["preferredPoolCount"] == "1"
    assert candidates["pairs"][0]["flashLoanLinkage"]["ready_for_atomic_arbitrage"] is False
    assert quotes["summary"]["routeCount"] == 0
    assert quotes["summary"]["probeNotionalUsd"] == 1000.0
    assert quotes["summary"]["completeStaticQuoteCount"] == 0
    assert quotes["summary"]["uniswapxQuoteableRouteCount"] == 0
    assert quotes["routes"] == []
    assert request["ok"] is False


def test_dex_quote_routes_keep_uniswap_v4_quote_requests(tmp_path, monkeypatch):
    cache_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        cache_path,
        {
            "network": "bnb",
            "chainId": 56,
            "pools": [
                {
                    "pool": "0x" + "9" * 40,
                    "symbolPair": "USDC:WBNB",
                    "dex": "uniswap_v4",
                    "protocol": "uniswap_v4",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WBNB",
                    "token0Usd": 600_000,
                    "token1Usd": 610_000,
                    "tvlUsd": 1_210_000,
                    "status": "recommended",
                }
            ],
        },
    )
    monkeypatch.setattr(
        pool_candidates,
        "query_flash_loan_pool_discovery",
        lambda chain_id=None: {"protocols": [], "removed_protocols": []},
    )
    monkeypatch.setattr(dex_surfaces, "dex_pool_candidates_payload", lambda chain_id=None, protocols=None: pool_candidates.dex_pool_candidates_payload(cache_path))

    quotes = dex_surfaces.dex_quote_routes_payload(chain_id=56)
    request = dex_surfaces.dex_quote_request_payload(chain_id=56, pair="USDC -> WBNB -> USDC", venue="uniswap_v4", amount="1000")

    assert quotes["summary"]["liveQuoterRequiredCount"] == 0
    assert quotes["routes"] == []
    assert request["ok"] is False


def test_dex_opportunity_scanner_records_uniswap_v4_adapter_required_routes(tmp_path):
    from runtime.dex_opportunity_scanner import DexOpportunityScanner

    recorded_batches = []

    def quote_payload_provider(*, chain_id=None, protocols=None):
        return {
            "poolStaticTable": {"network": "robinhood", "chainId": 4663},
            "summary": {
                "routeCount": 1,
                "completeStaticQuoteCount": 1,
                "executableCandidateCount": 0,
                "profitableRouteCount": 0,
                "liveQuoterRequiredCount": 1,
                "sdkAdapterRequiredCount": 1,
                "bestObservedNetProfitUsd": None,
            },
            "routes": [
                {
                    "symbolPair": "USDC -> WBNB -> DAI -> USDC",
                    "routeSymbols": ["USDC", "WBNB", "DAI", "USDC"],
                    "quoteStatus": "requires_live_quoter",
                    "overviewRoute": {"kind": "bridge_pair", "targetSymbol": "DAI"},
                    "quoteInputs": {
                        "candidatePools": [
                            {
                                "pool": "0x" + "9" * 40,
                                "dex": "uniswap_v4",
                                "protocol": "uniswap_v4",
                                "token0Symbol": "USDC",
                                "token1Symbol": "WBNB",
                            }
                        ],
                        "quoteRequests": [
                            {
                                "venue": "uniswap_v4",
                                "quoteMode": "uniswap_v4_sdk_exact_input_single",
                                "status": "sdk_adapter_required",
                            }
                        ],
                    },
                    "quoteAnalysis": {
                        "method": "static_amm_depth_probe",
                        "notionalUsd": 1000.0,
                        "inputSymbol": "USDC",
                        "outputSymbol": "USDC",
                        "finalOutputUsd": None,
                        "netProfitUsd": None,
                        "profitable": False,
                        "complete": True,
                        "requiresLiveQuoter": True,
                        "adapterRequired": True,
                        "executableCandidate": False,
                        "recommendation": {
                            "level": "requires_live_quote",
                            "action": "等待链上 Quoter 复核",
                            "reasons": ["v4 SDK adapter required"],
                        },
                        "steps": [
                            {
                                "hop": 1,
                                "fromSymbol": "USDC",
                                "toSymbol": "WBNB",
                                "executionTrust": "sdk_adapter_required",
                                "quoteSource": "uniswap_v4_sdk_adapter_required",
                            }
                        ],
                    },
                }
            ],
        }

    def record_attempts(attempts):
        recorded_batches.append(attempts)
        return {"recorded": len(attempts), "source": "jsonl", "error": None}

    def onchain_evidence_provider(route, **kwargs):
        return {
            "evidenceVersion": 1,
            "observedAt": kwargs.get("observed_at"),
            "network": "robinhood",
            "chainId": 4663,
            "status": "live_quote_failed",
            "executionTrust": "static_reference_only",
            "trustedForExecution": False,
            "blockNumber": None,
            "triggerContext": kwargs.get("trigger_context") or {},
            "triggerBlockNumber": (kwargs.get("trigger_context") or {}).get("triggerBlockNumber"),
            "quoteBlockSource": "trigger_block",
            "routeSymbols": route.get("routeSymbols") or [],
            "hops": [],
            "costModel": {},
            "blockers": ["uniswap v4 quote adapter is not configured"],
            "transactionDevelopment": {"status": "blocked_before_transaction_development", "executionEnabled": False, "blockers": ["uniswap v4 quote adapter is not configured"]},
        }

    scanner = DexOpportunityScanner(
        interval_seconds=3,
        record_cooldown_seconds=0,
        quote_payload_provider=quote_payload_provider,
        record_attempts=record_attempts,
        onchain_evidence_provider=onchain_evidence_provider,
        trigger_context_provider=lambda **kwargs: {
            "source": "test_trigger",
            "observedAt": kwargs.get("observed_at"),
            "network": "robinhood",
            "chainId": 4663,
            "available": True,
            "triggerBlockNumber": 12345,
            "blockNumber": 12345,
        },
        cache_path=tmp_path / "dex_opportunity_snapshot.json",
    )

    payload = scanner.scan_now(chain_id=4663, protocols=["uniswap_v4"])

    assert payload["snapshot"]["summary"]["sdk_adapter_required_count"] == 0
    assert payload["snapshot"]["summary"]["static_sdk_adapter_required_count"] == 1
    assert payload["snapshot"]["recording"]["candidate_count"] == 1
    assert payload["snapshot"]["recording"]["recorded"] == 1
    assert len(recorded_batches) == 1
    assert recorded_batches[0][0]["quote"]["live_onchain_evidence"]["status"] == "live_quote_failed"
    assert recorded_batches[0][0]["quote"]["quote_requests"][0]["status"] == "sdk_adapter_required"


