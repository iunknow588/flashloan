import inspect
import json
import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace

from dex.intent_trade import (
    bind_cow_intent_context,
    build_cow_intent_trade,
    build_triangular_onchain_intent_trade,
    pair_index_from_tokenx_tokeny,
    set_usdc_pair_memory_table,
    submit_cow_intent_trade,
)
from dex.intent_trade import direct
from dex.intent_trade import direct_utils
from dex.intent_trade import live_evidence
from dex.intent_trade.unified_live_signal_schema import (
    UNIFIED_LIVE_SIGNAL_REQUIRED_FIELDS,
    UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION,
    UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION,
    unified_live_signal_schema,
)


def _addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def _clear_direct_pair_env(monkeypatch):
    for name in (
        "TRIANGULAR_USDC_PAIR_ID",
        "TRIANGULAR_PAIR_ID",
        "TRIANGULAR_USDC_PAIRS_JSON",
        "TRIANGULAR_TOKEN_X",
        "TRIANGULAR_TOKEN_Y",
        "TRIANGULAR_DEX_ROUTER",
        "DEX_ROUTER_ADDRESS",
        "FUJI_DEX_ROUTER",
        "TRIANGULAR_RUNTIME_TRADES_JSON",
        "UNIFIED_EXECUTOR_ADDRESS",
        "TRIANGULAR_UNIFIED_EXECUTOR_ADDRESS",
        "UNIFIED_EXECUTOR_ARTIFACT",
        "TRIANGULAR_DIRECT_USE_DYNAMIC_CANDIDATES",
        "TRIANGULAR_DIRECT_CANDIDATE_STRATEGY",
        "TRIANGULAR_ENABLE_NON_USDC_CROSS_POOL",
        "TRIANGULAR_DIRECT_ENABLE_TOKEN_X_CROSS_POOL",
        "TRIANGULAR_MIN_PROFIT_USDC",
        "TRIANGULAR_MIN_PROFIT_USDC_BASE_UNITS",
        "TRIANGULAR_MIN_NET_PROFIT_USDC",
        "TRIANGULAR_MIN_NET_PROFIT_USDC_BASE_UNITS",
        "TRIANGULAR_EXECUTION_MIN_PROFIT_USDC",
        "TRIANGULAR_USDC_ADDRESS",
        "USDC_ADDRESS",
        "TRIANGULAR_DIRECT_NETWORK",
        "TRIANGULAR_ONCHAIN_NETWORK",
        "TRIANGULAR_TESTNET_NAME",
        "TRIANGULAR_NETWORK",
        "TRIANGULAR_TESTNET_CHAIN_ID",
        "ROBINHOOD_RPC_URL",
        "ROBINHOOD_RPC",
        "ROBINHOOD_RPCS",
        "ROBINHOOD_MAINNET_RPC_URL",
        "ROBINHOOD_MAINNET_RPC",
        "ROBINHOOD_TESTNET_RPC_URL",
        "ROBINHOOD_TESTNET_RPC",
        "ROBINHOOD_TESTNET_RPCS",
        "ROBINHOOD_USDC_ADDRESS",
        "ROBINHOOD_TESTNET_USDC_ADDRESS",
        "AVALANCHE_USDC_ADDRESS",
        "AVALANCHE_USDC",
        "AAVE_RESERVE_CACHE_FILE",
        "AVAX_USDC_PRICE",
        "GAS_TOKEN_USDC_PRICE",
        "AVAX_USDC_PRICE_UPDATED_AT",
        "GAS_TOKEN_USDC_PRICE_UPDATED_AT",
        "AVAX_USDC_PRICE_MAX_AGE_SECONDS",
        "GAS_TOKEN_USDC_PRICE_MAX_AGE_SECONDS",
        "UNIFIED_EXECUTOR_CACHE_RISK_PENALTY_USDC",
        "TRIANGULAR_CACHE_RISK_PENALTY_USDC",
        "UNIFIED_EXECUTOR_CACHE_RISK_PENALTY_PER_BLOCK_USDC",
        "TRIANGULAR_CACHE_RISK_PENALTY_PER_BLOCK_USDC",
        "UNIFIED_EXECUTOR_TOKEN_REGISTRY_FILE",
        "TRIANGULAR_TOKEN_REGISTRY_FILE",
        "DIRECT_ONCHAIN_PRE_PAUSE_FILE",
        "UNIFIED_EXECUTOR_PRE_PAUSE_FILE",
        "UNIFIED_EXECUTOR_PRICE_SOURCES_JSON",
        "UNIFIED_EXECUTOR_PRIVATE_RELAY_RESEARCH_ENABLED",
        "TRIANGULAR_PRIVATE_RELAY_RESEARCH_ENABLED",
        "LIQUIDATION_PRIVATE_RELAY_RESEARCH_ENABLED",
        "UNIFIED_EXECUTOR_ALLOW_PUBLIC_FALLBACK",
        "TRIANGULAR_ALLOW_PUBLIC_FALLBACK",
        "UNIFIED_EXECUTOR_PUBLIC_MEMPOOL_RISK_PENALTY_USDC",
        "TRIANGULAR_PUBLIC_MEMPOOL_RISK_PENALTY_USDC",
        "UNIFIED_EXECUTOR_PUBLIC_MEMPOOL_RISK_PENALTY_BPS",
        "TRIANGULAR_PUBLIC_MEMPOOL_RISK_PENALTY_BPS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(direct, "_USDC_PAIR_MEMORY_TABLE", [])
    monkeypatch.setattr(direct, "_UNIFIED_EXECUTOR_ABI_CACHE", None)


def _runtime_trades_json(token_x: str | None = None, token_y: str | None = None, pool: str | None = None) -> str:
    return json.dumps([
        {
            "tradeIndex": 0,
            "tokenX": token_x or _addr(101),
            "tokenY": token_y or _addr(102),
            "pools": [
                {"adapterKind": 1, "pool": pool or _addr(103)},
                {"adapterKind": 1, "pool": _addr(104)},
            ],
        }
    ])


def _unified_runtime_trades_json(
    usdc: str | None = None,
    token_x: str | None = None,
    token_y: str | None = None,
) -> str:
    usdc = usdc or _addr(500)
    token_x = token_x or _addr(501)
    token_y = token_y or _addr(502)
    return json.dumps([
        {
            "tradeIndex": 0,
            "tokenX": usdc,
            "tokenY": token_x,
            "strategyStatus": 1,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(601)}, {"adapterKind": 1, "pool": _addr(602)}],
        },
        {
            "tradeIndex": 1,
            "tokenX": usdc,
            "tokenY": token_y,
            "strategyStatus": 2,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(603)}, {"adapterKind": 1, "pool": _addr(604)}],
        },
        {
            "tradeIndex": 2,
            "tokenX": token_x,
            "tokenY": token_y,
            "strategyStatus": 3,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(605)}, {"adapterKind": 1, "pool": _addr(606)}],
        },
        {
            "tradeIndex": 3,
            "tokenX": token_y,
            "tokenY": token_x,
            "strategyStatus": 5,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(607)}, {"adapterKind": 1, "pool": _addr(608)}],
        },
    ])


def _live_runtime_trade(
    token_x: str | None = None,
    token_y: str | None = None,
    pool: str | None = None,
) -> dict:
    return {
        "tradeIndex": 0,
        "tokenX": token_x or _addr(500),
        "tokenY": token_y or _addr(501),
        "pools": [{"adapterKind": 1, "pool": pool or _addr(601)}],
    }


def _live_signal_result(
    *,
    runtime_trades: list[dict] | None = None,
    status: str = "static_call_passed",
    submitted: bool = False,
    request_overrides: dict | None = None,
) -> dict:
    request = {
        "runtimeTrades": runtime_trades if runtime_trades is not None else [_live_runtime_trade()],
        "deliveryPolicy": {
            "mode": "public_rpc_direct_after_fresh_gates",
            "privateRelayResearchEnabled": False,
            "privateFirst": False,
            "publicFallbackRequested": False,
            "publicFallbackRequiresRevalidation": False,
            "privacyBoundary": "deferred_to_cow_or_intent_layer",
        },
        "netProfitModel": {
            "enabled": "false",
            "relayCostWei": "0",
            "deliveryCostWei": "0",
            "publicMempoolRiskPenaltyUsdc": "0",
            "publicMempoolRiskPenaltyBps": "0",
        },
        "routeEvaluations": [
            {
                "netProfit": {
                    "relayCostWei": "0",
                    "relayCostUsdc": "0",
                    "deliveryCostWei": "0",
                    "deliveryCostUsdc": "0",
                    "publicMempoolRiskPenaltyUsdc": "0",
                    "publicMempoolRiskPenaltyFixedUsdc": "0",
                    "publicMempoolRiskPenaltyBps": "0",
                    "publicMempoolRiskPenaltyBpsUsdc": "0",
                }
            }
        ],
    }
    if request_overrides:
        request.update(request_overrides)
    return {
        "ok": status in {"static_call_passed", "submitted_success"},
        "submitted": submitted,
        "status": status,
        "request": request,
    }


def _market_row(symbol: str, change: float) -> dict:
    return {
        "symbol": f"{symbol}USDT",
        "base_symbol": symbol,
        "change_percent": change,
        "current_price": 1.0,
        "window_ready": True,
    }


def _pool_cache_for_symbols(symbols: list[str]) -> dict:
    token_by_symbol = {symbol: _addr(500 + index) for index, symbol in enumerate(symbols)}
    pools = []
    pool_id = 700
    for x_symbol in symbols:
        for y_symbol in symbols:
            if x_symbol == y_symbol:
                continue
            pools.append(
                {
                    "tokenX_symbol": x_symbol,
                    "tokenY_symbol": y_symbol,
                    "tokenX": token_by_symbol[x_symbol],
                    "tokenY": token_by_symbol[y_symbol],
                    "pools": [
                        {"adapterKind": 1, "pool": _addr(pool_id)},
                        {"adapterKind": 1, "pool": _addr(pool_id + 1)},
                        {"adapterKind": 2, "pool": _addr(pool_id + 2)},
                    ],
                }
            )
            pool_id += 3
    return {"network": "avalanche", "protocols": ["uniswap_v3"], "pools": pools}


def _pool_cache_with_metadata(symbols: list[str], *, chain_id: int = 43114, block_number: int = 100) -> dict:
    cache = _pool_cache_for_symbols(symbols)
    cache["chain_id"] = chain_id
    cache["block_number"] = block_number
    return cache


def test_prepare_unified_route_groups_rejects_single_pair_without_diagnostic_flag():
    usdc = _addr(500)
    trade = direct._normalize_runtime_trade(
        {
            "tradeIndex": 0,
            "tokenX": usdc,
            "tokenY": _addr(501),
            "strategyStatus": 1,
            "routeKey": "USDC:AAA",
            "pools": [{"adapterKind": 1, "pool": _addr(601)}, {"adapterKind": 1, "pool": _addr(602)}],
        },
        0,
    )
    trade["strategyStatus"] = 1
    trade["routeKey"] = "USDC:AAA"
    groups = [{"routeKey": "USDC:AAA", "routeKind": "usdc_cross_pool", "trades": [trade]}]

    prepared, rejected = direct._prepare_unified_route_groups(groups, usdc_address=usdc)
    diagnostic_prepared, _diagnostic_rejected = direct._prepare_unified_route_groups(
        groups,
        usdc_address=usdc,
        allow_single_pair_diagnostic=True,
    )

    assert prepared == []
    assert rejected[0]["reason"] == "single_pair_diagnostic_disabled"
    assert diagnostic_prepared[0]["routeCheck"]["routeDirection"] == "U-X-U"


def test_prepare_unified_route_groups_rejects_explicit_high_risk_token_metadata():
    usdc = _addr(500)
    token_x = _addr(501)
    token_y = _addr(502)
    groups = [
        {
            "routeKey": "AAA:BBB",
            "routeKind": "usdc_triangular",
            "tokenRisks": [
                {"token": token_x, "reentrancyRisk": "high"},
                {"token": token_y, "decimalTruncationRisk": "low"},
            ],
            "trades": [
                {
                    "tradeIndex": 0,
                    "tokenX": usdc,
                    "tokenY": token_x,
                    "strategyStatus": 1,
                    "pools": [{"adapterKind": 1, "pool": _addr(601)}],
                },
                {
                    "tradeIndex": 1,
                    "tokenX": usdc,
                    "tokenY": token_y,
                    "strategyStatus": 2,
                    "pools": [{"adapterKind": 1, "pool": _addr(602)}],
                },
                {
                    "tradeIndex": 2,
                    "tokenX": token_x,
                    "tokenY": token_y,
                    "strategyStatus": 3,
                    "pools": [{"adapterKind": 1, "pool": _addr(603)}],
                },
            ],
        }
    ]

    prepared, rejected = direct._prepare_unified_route_groups(groups, usdc_address=usdc)

    assert prepared == []
    assert rejected[0]["reason"] == "token_risk_broadcast_disabled"
    assert rejected[0]["tokenRisk"]["blocked"][0]["token"] == token_x


def test_prepare_unified_route_groups_requires_complete_token_risk_metadata_for_broadcast():
    usdc = _addr(500)
    token_x = _addr(501)
    token_y = _addr(502)
    groups = [
        {
            "routeKey": "AAA:BBB",
            "routeKind": "usdc_triangular",
            "tokenRisks": [{"token": usdc, "reentrancyRisk": "low"}],
            "trades": [
                {
                    "tradeIndex": 0,
                    "tokenX": usdc,
                    "tokenY": token_x,
                    "strategyStatus": 1,
                    "pools": [{"adapterKind": 1, "pool": _addr(601)}],
                },
                {
                    "tradeIndex": 1,
                    "tokenX": usdc,
                    "tokenY": token_y,
                    "strategyStatus": 2,
                    "pools": [{"adapterKind": 1, "pool": _addr(602)}],
                },
                {
                    "tradeIndex": 2,
                    "tokenX": token_x,
                    "tokenY": token_y,
                    "strategyStatus": 3,
                    "pools": [{"adapterKind": 1, "pool": _addr(603)}],
                },
            ],
        }
    ]

    prepared, rejected = direct._prepare_unified_route_groups(
        groups,
        usdc_address=usdc,
        require_token_risk_metadata=True,
    )

    assert prepared == []
    assert rejected[0]["reason"] == "token_risk_metadata_missing"
    assert set(rejected[0]["tokenRisk"]["missingTokens"]) == {token_x, token_y}


def test_runtime_route_group_candidates_preserve_explicit_risk_and_penalty_metadata():
    usdc = _addr(500)
    groups = direct._runtime_route_group_candidates(
        {
            "runtime_trades": [
                {
                    "tradeIndex": 0,
                    "tokenX": usdc,
                    "tokenY": _addr(501),
                    "pools": [{"adapterKind": 1, "pool": _addr(601)}],
                }
            ],
            "tokenRisks": [{"token": _addr(501), "reentrancyRisk": "low"}],
            "slippageRiskPenaltyUsdc": "250000",
            "routeCorrelationPenaltyUsdc": "500000",
        }
    )

    assert groups[0]["tokenRisks"][0]["reentrancyRisk"] == "low"
    assert groups[0]["slippageRiskPenaltyUsdc"] == "250000"
    assert groups[0]["routeCorrelationPenaltyUsdc"] == "500000"


def test_unified_route_group_check_accepts_single_usdc_cross_pool_trade():
    usdc = _addr(500)
    trade = direct._normalize_runtime_trade(
        {
            "tradeIndex": 0,
            "tokenX": usdc,
            "tokenY": _addr(501),
            "strategyStatus": 1,
            "routeKey": "USDC:AAA",
            "pools": [{"adapterKind": 1, "pool": _addr(601)}, {"adapterKind": 1, "pool": _addr(602)}],
        },
        0,
    )
    trade["strategyStatus"] = 1
    trade["routeKey"] = "USDC:AAA"

    ordered, plan = direct._ordered_runtime_trade_plan([trade], usdc_address=usdc)
    check = direct._unified_executor_route_order_check(ordered, usdc_address=usdc)

    assert plan["ok"] is True
    assert len(ordered) == 1
    assert check["ok"] is True
    assert check["routeDirection"] == "U-X-U"


def test_unified_route_group_selection_uses_highest_usdc_expected_profit():
    usdc = _addr(500)
    groups = [
        {"groupIndex": 0, "routeKey": "USDC:AAA", "routeKind": "usdc_cross_pool"},
        {"groupIndex": 1, "routeKey": "USDC:BBB", "routeKind": "usdc_cross_pool"},
    ]

    def preview(expected_profit: int):
        hop = (0, usdc, usdc, _addr(601), 0, 0, 0, 0)
        return (
            True,
            1,
            3,
            0,
            (True, 0, usdc, _addr(501), _addr(601), _addr(602), 500, 3000, 1, 1, -1, 1, 2, 2, 0),
            (usdc, usdc, 1_000_000, 0, 0, b"", 1_000_000 + expected_profit, 500, 1_000_000, 1_000_000, 1, expected_profit),
            (0, 0, 0, (hop, hop, hop), 0, 0, 0, 0),
            (1101, 1, 1, 1, 1, 0, 0, ((1, 2, 0, 0, 1101, 0, 0, 0, usdc, expected_profit, 1_000_000, 1_000_000),) * 5),
        )

    selected, report, evaluations = direct._select_best_unified_route_group(
        groups,
        usdc_address=usdc,
        preview_group=lambda group: preview(12 if group["routeKey"] == "USDC:AAA" else 34),
    )

    assert selected["routeKey"] == "USDC:BBB"
    assert report["executionPreview"]["expectedProfit"] == "34"
    assert [item["profitableCandidate"] for item in evaluations] == [True, True]
    assert [item["selected"] for item in evaluations] == [False, True]


def test_unified_route_group_selection_uses_net_profit_when_gas_model_is_supplied():
    usdc = _addr(500)
    groups = [
        {"groupIndex": 0, "routeKey": "USDC:AAA", "routeKind": "usdc_cross_pool"},
        {"groupIndex": 1, "routeKey": "USDC:BBB", "routeKind": "usdc_cross_pool"},
    ]

    def preview(expected_profit: int):
        hop = (0, usdc, usdc, _addr(601), 0, 0, 0, 0)
        return (
            True,
            1,
            3,
            0,
            (True, 0, usdc, _addr(501), _addr(601), _addr(602), 500, 3000, 1, 1, -1, 1, 2, 2, 0),
            (usdc, usdc, 1_000_000, 0, 0, b"", 1_000_000 + expected_profit, 500, 1_000_000, 1_000_000, 1, expected_profit),
            (0, 0, 0, (hop, hop, hop), 0, 0, 0, 0),
            (1101, 1, 1, 1, 1, 0, 0, ((1, 2, 0, 0, 1101, 0, 0, 0, usdc, expected_profit, 1_000_000, 1_000_000),) * 5),
        )

    selected, _report, evaluations = direct._select_best_unified_route_group(
        groups,
        usdc_address=usdc,
        preview_group=lambda group: preview(100 if group["routeKey"] == "USDC:AAA" else 120),
        estimate_group_gas=lambda group: 10 if group["routeKey"] == "USDC:AAA" else 100,
        gas_price_wei=10**18,
        avax_usdc_price_micro=1,
    )

    assert selected["routeKey"] == "USDC:AAA"
    assert [item["netProfit"]["netProfit"] for item in evaluations] == ["90", "20"]


def test_unified_route_group_selection_applies_slippage_and_correlation_penalties():
    usdc = _addr(500)
    groups = [
        {
            "groupIndex": 0,
            "routeKey": "USDC:AAA",
            "routeKind": "usdc_cross_pool",
            "slippageRiskPenaltyUsdc": 30,
            "routeCorrelationPenaltyUsdc": 20,
        },
        {
            "groupIndex": 1,
            "routeKey": "USDC:BBB",
            "routeKind": "usdc_cross_pool",
        },
    ]

    def preview(expected_profit: int):
        hop = (0, usdc, usdc, _addr(601), 0, 0, 0, 0)
        return (
            True,
            1,
            3,
            0,
            (True, 0, usdc, _addr(501), _addr(601), _addr(602), 500, 3000, 1, 1, -1, 1, 2, 2, 0),
            (usdc, usdc, 1_000_000, 0, 0, b"", 1_000_000 + expected_profit, 500, 1_000_000, 1_000_000, 1, expected_profit),
            (0, 0, 0, (hop, hop, hop), 0, 0, 0, 0),
            (1101, 1, 1, 1, 1, 0, 0, ((1, 2, 0, 0, 1101, 0, 0, 0, usdc, expected_profit, 1_000_000, 1_000_000),) * 5),
        )

    selected, _report, evaluations = direct._select_best_unified_route_group(
        groups,
        usdc_address=usdc,
        preview_group=lambda _group: preview(100),
    )

    assert selected["routeKey"] == "USDC:BBB"
    assert evaluations[0]["netProfit"]["slippageRiskPenaltyUsdc"] == "30"
    assert evaluations[0]["netProfit"]["routeCorrelationPenaltyUsdc"] == "20"
    assert [item["netProfit"]["netProfit"] for item in evaluations] == ["50", "100"]


def test_unified_route_group_selection_applies_public_mempool_risk_penalty():
    usdc = _addr(500)
    groups = [{"groupIndex": 0, "routeKey": "USDC:AAA", "routeKind": "usdc_cross_pool"}]

    def preview(expected_profit: int):
        hop = (0, usdc, usdc, _addr(601), 0, 0, 0, 0)
        return (
            True,
            1,
            3,
            0,
            (True, 0, usdc, _addr(501), _addr(601), _addr(602), 500, 3000, 1, 1, -1, 1, 2, 2, 0),
            (usdc, usdc, 1_000_000, 0, 0, b"", 1_001_000, 500, 1_000_000, 1_000_000, 1, expected_profit),
            (0, 0, 0, (hop, hop, hop), 0, 0, 0, 0),
            (1101, 1, 1, 1, 1, 0, 0, ((1, 2, 0, 0, 1101, 0, 0, 0, usdc, expected_profit, 1_000_000, 1_000_000),) * 5),
        )

    selected, _report, evaluations = direct._select_best_unified_route_group(
        groups,
        usdc_address=usdc,
        preview_group=lambda _group: preview(1_000),
        public_mempool_risk_penalty_usdc=100,
        public_mempool_risk_penalty_bps=1_000,
    )

    assert selected["routeKey"] == "USDC:AAA"
    assert evaluations[0]["netProfit"]["publicMempoolRiskPenaltyFixedUsdc"] == "100"
    assert evaluations[0]["netProfit"]["publicMempoolRiskPenaltyBps"] == "1000"
    assert evaluations[0]["netProfit"]["publicMempoolRiskPenaltyBpsUsdc"] == "100"
    assert evaluations[0]["netProfit"]["publicMempoolRiskPenaltyUsdc"] == "200"
    assert evaluations[0]["netProfit"]["netProfit"] == "800"


