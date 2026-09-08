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


def test_broadcast_gas_guard_requires_a_nonzero_price_under_the_configured_cap(monkeypatch):
    monkeypatch.setenv("TRIANGULAR_MAX_GAS_PRICE_WEI", "30000000000")

    accepted = direct._broadcast_gas_guard(
        200_000,
        SimpleNamespace(max_fee=25_000_000_000, strategy="normal"),
    )
    capped = direct._broadcast_gas_guard(
        200_000,
        SimpleNamespace(max_fee=30_000_000_001, strategy="normal"),
    )
    unavailable = direct._broadcast_gas_guard(
        200_000,
        SimpleNamespace(max_fee=0, strategy="blocked"),
    )

    assert accepted["ok"] is True
    assert accepted["report"]["estimatedCostWei"] == "5000000000000000"
    assert capped["status"] == "gas_price_cap_exceeded"
    assert unavailable["status"] == "gas_price_unavailable"


def test_gas_token_price_report_requires_fresh_timestamp(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    now = datetime(2026, 8, 12, 0, 2, tzinfo=timezone.utc)

    missing_timestamp = direct._gas_token_usdc_price_report(
        {"avaxUsdcPrice": "24.5"},
        now=now,
    )
    fresh = direct._gas_token_usdc_price_report(
        {
            "avaxUsdcPrice": "24.5",
            "avaxUsdcPriceUpdatedAt": "2026-08-12T00:01:30Z",
            "gasTokenPriceMaxAgeSeconds": 60,
        },
        now=now,
    )
    stale = direct._gas_token_usdc_price_report(
        {
            "avaxUsdcPrice": "24.5",
            "avaxUsdcPriceUpdatedAt": "2026-08-12T00:00:00Z",
            "gasTokenPriceMaxAgeSeconds": 60,
        },
        now=now,
    )

    assert missing_timestamp["reason"] == "gas_token_usdc_price_timestamp_missing"
    assert fresh["ok"] is True
    assert fresh["priceMicro"] == "24500000"
    assert fresh["ageSeconds"] == "30"
    assert stale["reason"] == "gas_token_usdc_price_stale"


def test_gas_token_price_report_accepts_unix_timestamp():
    report = direct._gas_token_usdc_price_report(
        {
            "gasTokenUsdcPrice": "25",
            "gasTokenUsdcPriceUpdatedAt": "1786492890",
            "gasTokenPriceMaxAgeSeconds": 60,
        },
        now=datetime(2026, 8, 12, 0, 2, tzinfo=timezone.utc),
    )

    assert report["ok"] is True
    assert report["priceMicro"] == "25000000"


def test_gas_token_price_report_requires_healthy_multi_source_for_broadcast():
    now = datetime(2026, 8, 12, 0, 2, tzinfo=timezone.utc)
    sources = [
        {"id": "chainlink_avax_usd", "kind": "chainlink-derived", "priceUsdc": "25.00", "updatedAt": "2026-08-12T00:01:50Z"},
        {"id": "independent_quote", "kind": "independent", "priceUsdc": "25.01", "updatedAt": "2026-08-12T00:01:55Z"},
    ]

    report = direct._gas_token_usdc_price_report(
        {
            "priceSourcePolicy": "multi-source-median",
            "priceSources": sources,
            "gasTokenPriceMaxAgeSeconds": 60,
            "priceMaxDeviationBps": 20,
        },
        now=now,
        require_production_sources=True,
    )
    blocked = direct._gas_token_usdc_price_report(
        {"priceSourcePolicy": "diagnostic-single-source", "priceSources": [sources[0]]},
        now=now,
        require_production_sources=True,
    )

    assert report["ok"] is True
    assert report["priceSourceCount"] == "2"
    assert report["priceSourcePolicy"] == "multi-source-median"
    assert blocked["reason"] == "gas_token_price_source_health_failed"


def test_direct_pre_pause_persists_and_blocks_new_broadcasts(tmp_path):
    path = tmp_path / "direct_pre_pause.json"

    active = direct.set_direct_pre_pause(
        True,
        reason="drill",
        set_by="test",
        pause_trigger_source="watchdog",
        pause_detected_at="2026-08-13T00:00:00+00:00",
        blocked_candidate_count=3,
        path=path,
    )
    cleared = direct.set_direct_pre_pause(False, reason="manual_resume", set_by="test", path=path)

    assert active["prePause"] is True
    assert active["reason"] == "drill"
    assert active["pauseTriggerSource"] == "watchdog"
    assert active["pauseDetectedAt"] == "2026-08-13T00:00:00+00:00"
    assert active["signerBlockedAt"] >= active["pauseDetectedAt"]
    assert int(active["pausePropagationMs"]) >= 0
    assert active["blockedCandidateCount"] == "3"
    assert cleared["prePause"] is False
    assert cleared["reason"] == "manual_resume"


def test_cache_risk_penalty_adds_per_block_age(monkeypatch):
    _clear_direct_pair_env(monkeypatch)
    monkeypatch.setenv("TRIANGULAR_CACHE_RISK_PENALTY_USDC", "0.25")
    monkeypatch.setenv("TRIANGULAR_CACHE_RISK_PENALTY_PER_BLOCK_USDC", "0.01")

    penalty = direct._cache_risk_penalty_usdc_base_units(
        cache_reports={
            "runtimePoolCache": {"ok": True, "currentBlock": "120", "cacheBlock": "100"},
            "aaveReserveCache": {"ok": True, "ageBlocks": "7"},
        }
    )

    assert penalty == 450_000


def test_submit_direct_onchain_trade_reports_incomplete_when_token_pair_is_not_in_memory_table(monkeypatch, tmp_path):
    _clear_direct_pair_env(monkeypatch)
    for name in ("AVALANCHE_RPC_URL", "AVALANCHE_RPC", "FUJI_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRIANGULAR_RUNTIME_POOL_CACHE_FILE", str(tmp_path / "missing_pool_cache.json"))
    set_usdc_pair_memory_table([{"tokenX": _addr(41), "tokenY": _addr(42)}])

    result = direct.submit_direct_onchain_trade(
        quote_payload={
            "cow_flashloan_intent": {
                "direct_onchain_protocol": {
                    "enabled": True,
                    "network": "avalanche",
                    "unified_executor_address": _addr(43),
                }
            }
        },
        opportunity={"tokenX": _addr(44), "tokenY": _addr(45)},
    )

    assert result["status"] == "direct_protocol_incomplete"
    assert result["error"] == "runtime_trades_empty"
    monkeypatch.setattr(direct, "_USDC_PAIR_MEMORY_TABLE", [])


def test_direct_bool_value_parses_string_false():
    assert direct_utils._bool_value("false", True) is False
    assert direct_utils._bool_value("0", True) is False
    assert direct_utils._bool_value("true", False) is True
    assert direct_utils._bool_value(None, True) is True


def test_direct_raw_signed_transaction_supports_web3_v6_field():
    assert direct_utils._raw_signed_transaction(SimpleNamespace(rawTransaction=b"raw")) == b"raw"


def test_direct_token_registry_falls_back_to_local_aave_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "aave_reserve_assets.json"
    cache_path.write_text(
        '{"assets":[{"symbol":"USDC","address":"0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E","decimals":6}]}',
        encoding="utf-8",
    )

    calls = []

    def fake_build_token_registry(**kwargs):
        calls.append(kwargs)
        if kwargs.get("include_cow_token_list", True):
            raise OSError("remote token list unavailable")
        return {"USDC": "local-usdc"}

    monkeypatch.setattr(direct_utils, "build_token_registry", fake_build_token_registry)

    registry = direct_utils._build_direct_token_registry(aave_cache_path=cache_path, network="avalanche")

    assert registry == {"USDC": "local-usdc"}
    assert calls[-1]["include_cow_token_list"] is False


def test_direct_route_decision_report_names_precise_failure():
    report = direct_utils._route_decision_report(
        [
            False,
            False,
            0,
            0,
            ["USDC", "JOE", "AI", "USDC"],
            0,
            1055,
            1000,
            0,
            3,
            1000500001,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            -70000,
        ]
    )

    assert report["failureCode"] == "3"
    assert report["failureReason"] == "middle_hop_quote_failed"
    assert report["path"] == ["USDC", "JOE", "AI", "USDC"]
    assert report["requiredFinalUsdc"] == "1000500001"
    assert report["mBps"] == "-70000"


def test_direct_execution_failure_report_decodes_executor_revert_data():
    values = [1, 1_000_500_000, 1_000_500_000, 0, 0, 0]
    raw = "0x" + direct_utils.EXECUTOR_ERROR_SELECTOR + "".join(f"{value:064x}" for value in values)

    report = direct_utils._execution_failure_report(Exception({"data": raw}))

    assert report["source"] == "aave_triangular_executor"
    assert report["failureCode"] == "1"
    assert report["failureReason"] == "post_swap_balance_below_actual_repayment"
    assert report["amountOutMinUsdc"] == "1000500000"


def test_direct_execution_failure_report_decodes_router_selector():
    raw = "0x" + direct_utils.ROUTER_SWAP_ERROR_SELECTOR + "deadbeef" + ("0" * 56)

    report = direct_utils._execution_failure_report(Exception({"data": raw}))

    assert report["source"] == "aave_triangular_executor"
    assert report["failureCode"] == "router_swap_failed"
    assert report["failureReason"] == "router_swap_reverted_selector_0xdeadbeef"


def test_direct_execution_failure_report_decodes_invalid_router_result():
    raw = "0x" + direct_utils.ROUTER_SWAP_RESULT_INVALID_SELECTOR + f"{3:064x}"

    report = direct_utils._execution_failure_report(Exception({"data": raw}))

    assert report["source"] == "aave_triangular_executor"
    assert report["failureCode"] == "router_swap_result_invalid"
    assert report["failureReason"] == "router_swap_result_length_invalid"
    assert report["resultLength"] == "3"
