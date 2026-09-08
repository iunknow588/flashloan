import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from dex.cow_flashloan.routes import CowToken
from market.strategy.arbitrage import ArbitrageConfig
from market.binance_market.service import (
    build_binance_market_state,
    build_cow_network_market_claims,
    build_cow_supported_market_overview,
    build_binance_rest_market_snapshot,
    build_cow_route_precheck,
    build_cow_quote_verification,
    _cow_pure_profit_intent,
    _cow_order_submission_enabled,
    load_cow_supported_token_registry,
    cost_adjusted_cow_thresholds,
    read_binance_market_snapshot,
    refresh_cow_supported_token_cache,
    select_binance_market_extremes,
    needs_binance_rest_snapshot,
    _cow_execution_precheck,
    _query_window_timing,
)
from core.db.storage_cow_execution import (
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
    _category_within_retention,
    append_cow_execution_attempts_jsonl,
    build_cow_execution_attempts,
    build_cow_market_claim_candidate_attempts,
    build_cow_market_claim_pairs,
    build_cow_market_candidate_attempts,
    load_recent_cow_execution_attempts_jsonl,
)


def _row(symbol: str, start: float, end: float) -> dict:
    return {
        "symbol": symbol,
        "start_price": start,
        "end_price": end,
        "current_price": end,
        "change_percent": (end - start) / start * 100,
        "price_source": "ws",
        "window_ready": True,
    }


def _sdk_ready_status() -> dict:
    return {
        "ready": True,
        "reason": "cow_flashloan_sdk_ready",
        "missing_packages": [],
        "install_command": "cd flashLoan\\flashloan\\app_runtime\\cow_flashloan\\node_adapter && npm install",
    }


def _sdk_missing_status() -> dict:
    return {
        "ready": False,
        "reason": "cow_flashloan_sdk_install_required",
        "missing_packages": ["@cowprotocol/sdk-flash-loans"],
        "install_command": "cd flashLoan\\flashloan\\app_runtime\\cow_flashloan\\node_adapter && npm install",
    }


def _fake_pool_filter(symbols: list[str], pairs: list[tuple[str, str]]) -> dict:
    pair_map = {
        tuple(sorted((left.upper(), right.upper()))): {
            "pair": f"{left.upper()}:{right.upper()}",
            "effectivePoolCount": 2,
        }
        for left, right in pairs
    }
    return {
        "loaded": True,
        "source": "test",
        "pair_count": len(pair_map),
        "symbols": [symbol.upper() for symbol in symbols],
        "pairs": pair_map,
        "cache_block_number": "1",
        "fetched_at": "test",
        "error": None,
    }


def test_cow_execution_attempt_history_can_fallback_to_jsonl(tmp_path):
    payload = {
        "cow_network": "avalanche",
        "cow_chain_id": 43114,
        "owner": "0x" + "8" * 40,
        "ranking": [
            {
                "pair": "AAA / BBB",
                "pair_rank": 1,
                "priority_reason": "buy_loser_then_gainer",
                "path": ["USDC", "BBB", "AAA", "USDC"],
                "final_delta_amount": "1.2",
                "final_symbol": "USDC",
                "execution_precheck": {
                    "status": "checks_passed_order_disabled",
                    "checks_passed": True,
                    "can_submit_order": False,
                    "order_submission_enabled": False,
                    "auto_execute_requested": True,
                    "reasons": ["order_submission_adapter_unavailable"],
                },
            }
        ],
    }

    attempts = build_cow_execution_attempts(
        payload,
        market_state={"observed_at": "2026-08-05T00:00:00+00:00", "window_seconds": 3},
    )
    path = tmp_path / "cow_execution_attempts.jsonl"

    assert append_cow_execution_attempts_jsonl(path, attempts) == 1
    rows = load_recent_cow_execution_attempts_jsonl(path, limit=10)
    assert rows[0]["network"] == "avalanche"
    assert rows[0]["state"] == "checks_passed_order_disabled"
    assert rows[0]["blocked_reasons"] == ["order_submission_adapter_unavailable"]


def test_cow_market_candidate_history_records_displayed_bnb_routes(tmp_path):
    market_state = {
        "observed_at": "2026-08-05T00:00:00+00:00",
        "window_seconds": 3,
        "price_source": "binance_ws",
        "cow_filter": {"network": "bnb", "chain_id": 56},
        "pairs": [
            {
                "rank": 1,
                "pair": "AAAUSDT / BBBUSDT",
                "x_symbol": "AAAUSDT",
                "y_symbol": "BBBUSDT",
                "x_base_symbol": "AAA",
                "y_base_symbol": "BBB",
                "x_change_percent": 2.5,
                "y_change_percent": -1.5,
                "x_start_price": 1.0,
                "x_current_price": 1.025,
                "y_start_price": 2.0,
                "y_current_price": 1.97,
                "window_spread_percent": 4.0,
                "quote_required": True,
                "candidate_basis": "binance_token_names_only",
                "blocked_reasons": ["requires_cow_or_dex_quote"],
                "route_results": [
                    {
                        "route_no": 1,
                        "route": ["USDC", "BBB", "AAA", "USDC"],
                        "initial_amount": "1000",
                        "initial_symbol": "USDC",
                        "priority_reason": "buy_loser_then_gainer",
                        "quote_required": True,
                        "binance_execution_plan": {
                            "available": True,
                            "initial_amount": "1000",
                            "initial_symbol": "USDC",
                            "final_target_amount": "1012",
                            "final_symbol": "USDC",
                            "profit_amount": "12",
                            "profit_percent": "1.2",
                            "steps": [],
                        },
                    },
                ],
            }
        ],
    }

    attempts = build_cow_market_candidate_attempts(market_state)

    assert len(attempts) == 1
    assert {attempt["network"] for attempt in attempts} == {"bnb"}
    assert {attempt["chain_id"] for attempt in attempts} == {56}
    assert {attempt["execution_phase"] for attempt in attempts} == {"market_candidate"}
    assert {attempt["state"] for attempt in attempts} == {"quote_required"}
    assert attempts[0]["precheck"]["reasons"] == ["requires_cow_or_dex_quote"]
    assert attempts[0]["quote"]["window_spread_percent"] == 4.0
    path = tmp_path / "cow_execution_attempts.jsonl"
    assert append_cow_execution_attempts_jsonl(path, attempts) == 1
    rows = load_recent_cow_execution_attempts_jsonl(path, limit=10, networks=["bnb"])
    summary = rows[0]["review_summary"]
    assert summary["window_spread_percent"] == 4.0
    assert summary["market_prices"][0]["start_price"] == 1.0
    assert summary["market_prices"][1]["current_price"] == 1.97
    assert summary["plan"]["profit_amount"] == "12"
    assert summary["plan"]["profit_percent"] == "1.2"
    assert summary["profit_guard"]["status"] == "quote_required"


def test_cow_market_claim_history_records_displayed_chain_candidates():
    claims = [
        {
            "network": "bnb",
            "chain_id": 56,
            "min_spread_percent": 1.0,
            "threshold_detail": {"amount": "1000", "adjusted_min_spread_percent": "1.0"},
            "top": [
                {
                    "symbol": "AAAUSDT",
                    "base_symbol": "AAA",
                    "change_percent": 2.5,
                    "start_price": 1,
                    "current_price": 1.025,
                }
            ],
            "bottom": [
                {
                    "symbol": "BBBUSDT",
                    "base_symbol": "BBB",
                    "change_percent": -1.2,
                    "start_price": 2,
                    "current_price": 1.976,
                }
            ],
        }
    ]

    attempts = build_cow_market_claim_candidate_attempts(
        claims,
        observed_at="2026-08-05T00:00:00+00:00",
        price_source="binance_ws",
    )

    assert len(attempts) == 2
    assert {attempt["network"] for attempt in attempts} == {"bnb"}
    assert {attempt["pair"] for attempt in attempts} == {"AAAUSDT / BBBUSDT"}
    assert {attempt["execution_phase"] for attempt in attempts} == {"market_candidate"}
    assert {attempt["quote"]["candidate_basis"] for attempt in attempts} == {"cow_network_claim_top_bottom"}
    assert {tuple(attempt["route_path"]) for attempt in attempts} == {
        ("USDC", "BBB", "AAA", "USDC"),
        ("USDC", "AAA", "BBB", "USDC"),
    }


def test_cow_market_claim_pairs_can_feed_quote_verification(monkeypatch):
    claim = {
        "network": "bnb",
        "chain_id": 56,
        "min_spread_percent": 1.0,
        "threshold_detail": {"amount": "1000", "adjusted_min_spread_percent": "1.0"},
        "top": [
            {"symbol": "AAAUSDT", "base_symbol": "AAA", "change_percent": 2.5, "start_price": 1, "current_price": 1.025}
        ],
        "bottom": [
            {"symbol": "BBBUSDT", "base_symbol": "BBB", "change_percent": -1.2, "start_price": 2, "current_price": 1.976}
        ],
    }

    pairs = build_cow_market_claim_pairs(claim)
    market_state = {"observed_at": "2026-08-05T00:00:00+00:00", "pairs": pairs}
    registry = {
        symbol: CowToken(symbol, "0x" + str(index) * 40, 18, "test")
        for index, symbol in enumerate(["USDC", "AAA", "BBB"], start=1)
    }
    monkeypatch.setattr(
        "market.binance_market.service.evaluate_cow_route",
        lambda route, **kwargs: {
            "name": route["name"],
            "path": route["path"],
            "input_amount": "1000",
            "input_symbol": "USDC",
            "final_symbol": "USDC",
            "final_amount": "1001",
            "viable": True,
            "hops": [],
        },
    )
    payload = build_cow_quote_verification(market_state, quote_limit=1, registry=registry)

    assert len(pairs) == 1
    assert pairs[0]["pair"] == "AAAUSDT / BBBUSDT"
    assert payload["route_count"] == 2
    assert all(route["supported"] is True for route in payload["precheck"]["routes"])


def test_cow_market_claim_pairs_can_quote_displayed_rows_below_spread_floor():
    claim = {
        "network": "bnb",
        "chain_id": 56,
        "min_spread_percent": 1.0,
        "threshold_detail": {"amount": "1000", "adjusted_min_spread_percent": "1.0"},
        "top": [
            {"symbol": "AAAUSDT", "base_symbol": "AAA", "change_percent": 0.2, "start_price": 1, "current_price": 1.002}
        ],
        "bottom": [
            {"symbol": "BBBUSDT", "base_symbol": "BBB", "change_percent": -0.1, "start_price": 2, "current_price": 1.998}
        ],
    }

    strict_pairs = build_cow_market_claim_pairs(claim)
    quoted_display_pairs = build_cow_market_claim_pairs(
        claim,
        include_below_min_spread=True,
        max_pairs=1,
    )

    assert strict_pairs == []
    assert len(quoted_display_pairs) == 1
    assert quoted_display_pairs[0]["candidate_basis"] == "cow_network_claim_top_bottom_below_spread"
    assert quoted_display_pairs[0]["blocked_reasons"] == ["spread_below_dynamic_min", "requires_cow_or_dex_quote"]


def test_cow_market_candidate_jsonl_keeps_two_day_bucket_window_and_dedupes(tmp_path):
    path = tmp_path / "cow_execution_attempts.jsonl"
    old_created_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    recent_created_at = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    old_row = {
        "created_at": old_created_at,
        "observed_at": old_created_at,
        "network": "bnb",
        "pair": "OLD / ROW",
        "route_path": ["USDC", "OLD", "ROW", "USDC"],
        "execution_phase": "market_candidate",
        "state": "quote_required",
    }
    recent_polygon_row = {
        "created_at": recent_created_at,
        "observed_at": recent_created_at,
        "network": "polygon",
        "pair": "POL / ROW",
        "route_path": ["USDC", "POL", "ROW", "USDC"],
        "execution_phase": "market_candidate",
        "state": "quote_required",
    }
    path.write_text("\n".join(json.dumps(row) for row in [old_row, recent_polygon_row]) + "\n", encoding="utf-8")
    bnb_attempt = {
        "observed_at": "2026-08-05T00:00:00+00:00",
        "network": "bnb",
        "chain_id": 56,
        "pair": "AAA / BBB",
        "pair_rank": 1,
        "priority_reason": "buy_loser_then_gainer",
        "route_path": ["USDC", "BBB", "AAA", "USDC"],
        "execution_phase": "market_candidate",
        "state": "quote_required",
        "checks_passed": False,
        "can_submit_order": False,
        "blocked_reasons": ["requires_cow_or_dex_quote"],
    }
    polygon_attempt = {**bnb_attempt, "network": "polygon", "pair": "POL / ROW"}

    assert append_cow_execution_attempts_jsonl(path, [bnb_attempt]) == 1
    assert append_cow_execution_attempts_jsonl(path, [bnb_attempt]) == 0
    assert append_cow_execution_attempts_jsonl(path, [polygon_attempt], dedupe_market_candidates=False) == 1

    bnb_rows = load_recent_cow_execution_attempts_jsonl(path, limit=10, networks=["bnb"])
    per_network_rows = load_recent_cow_execution_attempts_jsonl(path, limit=1, networks=["bnb", "polygon"])
    all_rows = load_recent_cow_execution_attempts_jsonl(path, limit=10)

    assert [row["pair"] for row in bnb_rows] == ["AAA / BBB"]
    assert {row["network"] for row in per_network_rows} == {"bnb", "polygon"}
    assert {row["pair"] for row in all_rows} == {"AAA / BBB", "POL / ROW"}


def test_cow_execution_attempt_jsonl_uses_category_retention(tmp_path):
    path = tmp_path / "cow_execution_attempts.jsonl"
    old_not_executable = {
        "created_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
        "observed_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
        "network": "bnb",
        "pair": "OLD / BLOCKED",
        "route_path": ["USDC", "OLD", "BLOCKED", "USDC"],
        "execution_phase": "market_candidate",
        "state": "quote_required",
        "checks_passed": False,
        "can_submit_order": False,
    }
    recent_failed = {
        "created_at": (datetime.now(timezone.utc) - timedelta(days=6)).isoformat(),
        "observed_at": (datetime.now(timezone.utc) - timedelta(days=6)).isoformat(),
        "network": "bnb",
        "pair": "RECENT / FAILED",
        "route_path": ["USDC", "RECENT", "FAILED", "USDC"],
        "execution_phase": "execution",
        "state": "execution_failed",
        "checks_passed": True,
        "can_submit_order": True,
    }
    old_failed = {
        **recent_failed,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
        "observed_at": (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
        "pair": "OLD / FAILED",
    }
    old_success = {
        "created_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        "observed_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        "network": "bnb",
        "pair": "OLD / SUCCESS",
        "route_path": ["USDC", "OLD", "SUCCESS", "USDC"],
        "execution_phase": "settled",
        "state": "settled",
        "checks_passed": True,
        "can_submit_order": True,
    }
    path.write_text(
        "\n".join(json.dumps(row) for row in [old_not_executable, recent_failed, old_failed, old_success]) + "\n",
        encoding="utf-8",
    )

    assert append_cow_execution_attempts_jsonl(path, []) == 0
    assert append_cow_execution_attempts_jsonl(path, [{"network": "bnb", "pair": "NEW / BLOCKED", "execution_phase": "market_candidate", "state": "quote_required"}]) == 1

    not_executable = load_recent_cow_execution_attempts_jsonl(path, limit=10, category=COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE)
    failed = load_recent_cow_execution_attempts_jsonl(path, limit=10, category=COW_ATTEMPT_CATEGORY_EXECUTION_FAILED)
    success = load_recent_cow_execution_attempts_jsonl(path, limit=10, category=COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS)
    all_pairs = {row["pair"] for row in load_recent_cow_execution_attempts_jsonl(path, limit=20)}

    assert [row["pair"] for row in not_executable] == ["NEW / BLOCKED"]
    assert [row["pair"] for row in failed] == ["RECENT / FAILED"]
    assert [row["pair"] for row in success] == ["OLD / SUCCESS"]
    assert all_pairs == {"NEW / BLOCKED", "RECENT / FAILED", "OLD / SUCCESS"}
    assert not_executable[0]["review_category"] == COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE
    assert failed[0]["review_category"] == COW_ATTEMPT_CATEGORY_EXECUTION_FAILED
    assert success[0]["review_category"] == COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS


def test_cow_not_executable_retention_uses_two_local_day_buckets():
    local_tz = timezone(timedelta(hours=8))
    now = datetime(2026, 8, 5, 0, 1, tzinfo=local_tz)
    previous_day_start = {
        "created_at": datetime(2026, 8, 4, 0, 0, tzinfo=local_tz).isoformat(),
        "execution_phase": "market_candidate",
        "state": "quote_required",
    }
    before_previous_day = {
        **previous_day_start,
        "created_at": datetime(2026, 8, 3, 23, 59, 59, tzinfo=local_tz).isoformat(),
    }

    assert _category_within_retention(previous_day_start, now=now) is True
    assert _category_within_retention(before_previous_day, now=now) is False


def test_cow_failed_retention_uses_two_local_week_buckets():
    local_tz = timezone(timedelta(hours=8))
    now = datetime(2026, 8, 10, 0, 1, tzinfo=local_tz)
    previous_week_start = {
        "created_at": datetime(2026, 8, 3, 0, 0, tzinfo=local_tz).isoformat(),
        "execution_phase": "execution",
        "state": "execution_failed",
        "checks_passed": True,
        "can_submit_order": True,
    }
    before_previous_week = {
        **previous_week_start,
        "created_at": datetime(2026, 8, 2, 23, 59, 59, tzinfo=local_tz).isoformat(),
    }

    assert _category_within_retention(previous_week_start, now=now) is True
    assert _category_within_retention(before_previous_week, now=now) is False


def test_cow_network_options_exposes_supported_networks():
    from market.binance_market.service import cow_network_options

    options = cow_network_options()

    assert options["default_network"] == "bnb"
    assert {item["network"] for item in options["networks"]} == {
        "ethereum",
        "gnosis",
        "arbitrum_one",
        "base",
        "polygon",
        "avalanche",
        "bnb",
        "linea",
        "plasma",
        "ink",
        "sepolia",
    }
    assert any(item["label"] == "BNB Chain" for item in options["networks"])
    assert any(item["network"] == "sepolia" and item["testnet"] is True for item in options["networks"])


def test_cow_supported_token_cache_refreshes_file_and_loads_registry(tmp_path, monkeypatch):
    cache_path = tmp_path / "cow_tokens.json"
    monkeypatch.setattr(
        "market.binance_market.service.load_cow_token_list",
        lambda *args, **kwargs: [
            CowToken("USDC", "0x" + "1" * 40, 6, "test"),
            CowToken("WETH", "0x" + "2" * 40, 18, "test"),
        ],
    )

    refreshed = refresh_cow_supported_token_cache(cow_network="sepolia", cache_path=cache_path)
    loaded = load_cow_supported_token_registry(
        cow_network="sepolia",
        cache_path=cache_path,
        allow_live_fallback=False,
    )

    assert refreshed["token_count"] == 2
    assert loaded["source"] == "memory"
    assert loaded["token_count"] == 2
    assert set(loaded["registry"]) >= {"USDC", "WETH"}


def test_cow_supported_token_cache_dedupes_duplicate_addresses(tmp_path, monkeypatch):
    cache_path = tmp_path / "cow_tokens.json"
    monkeypatch.setattr(
        "market.binance_market.service.load_cow_token_list",
        lambda *args, **kwargs: [
            CowToken("AAA", "0x" + "1" * 40, 18, "test"),
            CowToken("AAA2", "0x" + "1" * 40, 18, "test"),
            CowToken("BBB", "0x" + "2" * 40, 18, "test"),
        ],
    )

    refreshed = refresh_cow_supported_token_cache(cow_network="polygon", cache_path=cache_path)
    loaded = load_cow_supported_token_registry(
        cow_network="polygon",
        cache_path=cache_path,
        allow_live_fallback=False,
    )

    assert refreshed["token_count"] == 2
    assert loaded["token_count"] == 2
    assert {token["address"] for token in loaded["tokens"]} == {"0x" + "1" * 40, "0x" + "2" * 40}


def test_cow_quote_verification_marks_requested_owner_source(monkeypatch):
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {"rank": 1, "pair": "AAA / BBB", "x_base_symbol": "AAA", "y_base_symbol": "BBB"},
        ],
    }

    monkeypatch.setattr(
        "market.binance_market.service.build_token_registry",
        lambda *args, **kwargs: {"AAA": object(), "BBB": object()},
    )
    monkeypatch.setattr(
        "market.binance_market.service.evaluate_cow_route",
        lambda route, **kwargs: {
            "name": route["name"],
            "path": route["path"],
            "input_amount": "1000",
            "input_symbol": route["path"][0],
            "final_symbol": route["path"][-1],
            "final_amount_units": "1200",
            "final_amount": "1200",
            "viable": True,
            "hops": [],
        },
    )
    monkeypatch.setattr(
        "market.binance_market.service.rank_cow_routes",
        lambda items: items,
    )

    payload = build_cow_quote_verification(market_state, owner="0x" + "7" * 40)

    assert payload["owner_source"] == "request.owner"


def test_cow_quote_verification_keeps_failed_unknown_token_routes():
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {
                "rank": 1,
                "pair": "PYRUSDT / ICXUSDT",
                "x_base_symbol": "PYR",
                "y_base_symbol": "ICX",
                "x_start_price": 0.062,
                "x_current_price": 0.063,
                "y_start_price": 0.0216,
                "y_current_price": 0.0215,
            },
        ],
    }

    payload = build_cow_quote_verification(
        market_state,
        quote_limit=1,
        registry={},
    )

    assert payload["route_count"] == 2
    assert payload["viable_count"] == 0
    assert len(payload["ranking"]) == 2
    assert {item["viable"] for item in payload["ranking"]} == {False}
    assert all("unsupported CoW tokens on avalanche" in item["error"] for item in payload["ranking"])
    assert all(item["cow_support"]["supported"] is False for item in payload["ranking"])


def test_cow_route_precheck_marks_unsupported_tokens_before_quote():
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {
                "rank": 1,
                "pair": "PYRUSDT / ICXUSDT",
                "x_base_symbol": "PYR",
                "y_base_symbol": "ICX",
                "x_start_price": 0.062,
                "x_current_price": 0.063,
                "y_start_price": 0.0216,
                "y_current_price": 0.0215,
            },
        ],
    }

    payload = build_cow_route_precheck(
        market_state,
        quote_limit=1,
        registry={},
    )

    assert payload["route_count"] == 2
    assert payload["supported_route_count"] == 0
    assert payload["unsupported_route_count"] == 2
    assert {route["status"] for route in payload["routes"]} == {"unsupported_tokens"}
    assert {tuple(route["unsupported_tokens"]) for route in payload["routes"]} == {
        ("USDC", "ICX", "PYR"),
        ("USDC", "PYR", "ICX"),
    }
