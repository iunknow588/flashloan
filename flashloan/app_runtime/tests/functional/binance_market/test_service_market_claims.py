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


def test_cow_network_market_claims_only_lists_avalanche_pool_supported_set(monkeypatch):
    extremes = {
        "basket": [
            _row("AAAUSDT", 10.0, 10.8),
            _row("BBBUSDT", 10.0, 9.4),
            _row("CCCUSDT", 10.0, 10.7),
            _row("DDDUSDT", 10.0, 9.3),
            _row("PEPEUSDT", 0.000002, 0.000003),
        ],
        "sample_count": 5,
    }
    monkeypatch.setattr(
        "market.binance_market.service._load_avalanche_v3_pool_filter",
        lambda: _fake_pool_filter(
            ["USDC", "AAA", "BBB"],
            [("USDC", "AAA"), ("USDC", "BBB"), ("AAA", "BBB")],
        ),
    )

    claims = build_cow_network_market_claims(
        extremes,
        {
            "avalanche": {
                "source": "test",
                "token_count": 3,
                "registry": {
                    "AAA": CowToken("AAA", "0x" + "1" * 40, 18, "test"),
                    "BBB": CowToken("BBB", "0x" + "2" * 40, 18, "test"),
                },
            },
            "bnb": {
                "source": "test",
                "token_count": 3,
                "registry": {
                    "CCC": CowToken("CCC", "0x" + "3" * 40, 18, "test"),
                    "DDD": CowToken("DDD", "0x" + "4" * 40, 18, "test"),
                    "PEPE": CowToken("PEPE", "0x" + "5" * 40, 18, "test"),
                },
            },
            "sepolia": {"source": "test", "token_count": 1, "registry": {}},
        },
        limit=50,
        min_spread_percent=1.0,
        min_side_change_percent=0.5,
    )

    by_network = {item["network"]: item for item in claims}
    assert "sepolia" not in by_network
    assert "bnb" not in by_network
    assert [row["base_symbol"] for row in by_network["avalanche"]["top"]] == ["AAA"]
    assert [row["base_symbol"] for row in by_network["avalanche"]["bottom"]] == ["BBB"]
    assert by_network["avalanche"]["market_excluded_symbol_count"] == 1
    assert by_network["avalanche"]["min_side_change_percent"] == 0.3
    assert by_network["avalanche"]["pair_count"] == 1
    assert by_network["avalanche"]["pool_filter"]["rejected_pair_count"] == 0


def test_cow_network_market_claims_excludes_avalanche_tokens_without_pool_route(monkeypatch):
    extremes = {
        "basket": [
            _row("AAAUSDT", 10.0, 10.8),
            _row("BBBUSDT", 10.0, 9.4),
        ],
        "sample_count": 2,
    }
    shared_registry = {
        "AAA": CowToken("AAA", "0x" + "1" * 40, 18, "test"),
        "BBB": CowToken("BBB", "0x" + "2" * 40, 18, "test"),
    }

    monkeypatch.setattr(
        "market.binance_market.service._load_avalanche_v3_pool_filter",
        lambda: _fake_pool_filter(["USDC", "AAA", "BBB"], [("USDC", "AAA"), ("USDC", "BBB")]),
    )

    claims = build_cow_network_market_claims(
        extremes,
        {
            "avalanche": {"source": "test", "token_count": 2, "registry": shared_registry},
            "polygon": {"source": "test", "token_count": 2, "registry": shared_registry},
        },
        limit=1,
        min_spread_percent=1.0,
    )

    by_network = {item["network"]: item for item in claims}
    assert "polygon" not in by_network
    assert [row["base_symbol"] for row in by_network["avalanche"]["top"]] == ["AAA"]
    assert [row["base_symbol"] for row in by_network["avalanche"]["bottom"]] == ["BBB"]
    assert by_network["avalanche"]["top"][0]["pool_route_supported"] is False
    assert by_network["avalanche"]["bottom"][0]["pool_route_supported"] is False
    assert by_network["avalanche"]["pair_count"] == 0
    assert by_network["avalanche"]["pool_filter"]["rejected_pair_count"] == 1


def test_cow_supported_market_overview_lists_union_top_bottom_50_without_chain_exclusion():
    extremes = {
        "basket": [
            _row("AAAUSDT", 10.0, 10.8),
            _row("CCCUSDT", 10.0, 10.7),
            _row("BBBUSDT", 10.0, 9.4),
            _row("DDDUSDT", 10.0, 9.3),
            _row("EEEUSDT", 10.0, 10.9),
        ],
        "sample_count": 5,
    }
    shared_registry = {
        "AAA": CowToken("AAA", "0x" + "1" * 40, 18, "test"),
        "BBB": CowToken("BBB", "0x" + "2" * 40, 18, "test"),
    }
    overview = build_cow_supported_market_overview(
        extremes,
        {
            "avalanche": {"source": "test", "token_count": 2, "registry": shared_registry},
            "polygon": {"source": "test", "token_count": 3, "registry": shared_registry | {"CCC": CowToken("CCC", "0x" + "3" * 40, 18, "test")}},
            "bnb": {"source": "test", "token_count": 1, "registry": {"DDD": CowToken("DDD", "0x" + "4" * 40, 18, "test")}},
        },
        limit=50,
        min_side_change_percent=0.5,
    )

    assert overview["limit"] == 50
    assert overview["source_market_row_count"] == 5
    assert overview["market_eligible_symbol_count"] == 5
    assert overview["market_excluded_symbol_count"] == 0
    assert overview["market_excluded_reason_counts"] == {}
    assert overview["supported_symbol_count"] == 4
    assert overview["unsupported_symbol_count"] == 1
    assert overview["min_side_change_percent"] == 0.3
    assert overview["min_token_price_usd"] == 0.01
    assert [row["base_symbol"] for row in overview["top"]] == ["AAA", "CCC"]
    assert [row["base_symbol"] for row in overview["bottom"]] == ["DDD", "BBB"]
    aaa = overview["top"][0]
    assert aaa["cow_networks"] == ["avalanche", "polygon"]
    assert aaa["cow_network_count"] == 2


def test_cow_supported_market_overview_uses_only_side_move_price_and_cow_support_filters():
    extremes = {
        "basket": [
            _row("GAINUSDT", 10.0, 10.04),
            _row("GAINLOWMOVEUSDT", 10.0, 10.004),
            _row("GAINLOWPRICEUSDT", 0.009, 0.0092),
            _row("GAINUNSUPPORTEDUSDT", 10.0, 10.04),
            _row("LOSSUSDT", 10.0, 9.96),
            _row("LOSSLOWMOVEUSDT", 10.0, 9.996),
            _row("LOSSLOWPRICEUSDT", 0.0092, 0.009),
            _row("LOSSUNSUPPORTEDUSDT", 10.0, 9.96),
        ],
        "observation_universe_size": 8,
        "sample_count": 8,
    }
    registry = {
        "GAIN": CowToken("GAIN", "0x" + "1" * 40, 18, "test"),
        "GAINLOWMOVE": CowToken("GAINLOWMOVE", "0x" + "2" * 40, 18, "test"),
        "GAINLOWPRICE": CowToken("GAINLOWPRICE", "0x" + "3" * 40, 18, "test"),
        "LOSS": CowToken("LOSS", "0x" + "4" * 40, 18, "test"),
        "LOSSLOWMOVE": CowToken("LOSSLOWMOVE", "0x" + "5" * 40, 18, "test"),
        "LOSSLOWPRICE": CowToken("LOSSLOWPRICE", "0x" + "6" * 40, 18, "test"),
    }

    overview = build_cow_supported_market_overview(
        extremes,
        {"avalanche": {"source": "test", "token_count": len(registry), "registry": registry}},
        limit=50,
        min_side_change_percent=9.0,
        threshold_detail={"min_window_spread_percent": "99"},
    )

    assert overview["min_side_change_percent"] == 0.3
    assert [row["base_symbol"] for row in overview["top"]] == ["GAIN", "GAINLOWMOVE"]
    assert [row["base_symbol"] for row in overview["bottom"]] == ["LOSS", "LOSSLOWMOVE"]
    assert overview["supported_symbol_count"] == 4
    assert overview["candidate_supported_symbol_count"] == 2
    assert overview["top_filter"]["market_eligible_symbol_count"] == 2
    assert overview["top_filter"]["supported_symbol_count"] == 1
    assert overview["top_filter"]["unsupported_symbol_count"] == 1
    assert overview["bottom_filter"]["market_eligible_symbol_count"] == 2
    assert overview["bottom_filter"]["supported_symbol_count"] == 1
    assert overview["bottom_filter"]["unsupported_symbol_count"] == 1


def test_binance_market_snapshot_fills_full_market_gainers_and_losers(monkeypatch):
    previous = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "basket": [
            {"symbol": "AAAUSDT", "current_price": 1.0, "end_ms": 1785801600000},
            {"symbol": "BBBUSDT", "current_price": 2.0, "end_ms": 1785801600000},
            {"symbol": "CCCUSDT", "current_price": 5.0, "end_ms": 1785801600000},
            {"symbol": "DDDUSDT", "current_price": 4.0, "end_ms": 1785801600000},
        ],
    }
    current_rows = [
        {"symbol": "AAAUSDT", "current_price": 1.2},
        {"symbol": "BBBUSDT", "current_price": 2.2},
        {"symbol": "CCCUSDT", "current_price": 4.0},
        {"symbol": "DDDUSDT", "current_price": 3.8},
    ]

    snapshot = build_binance_rest_market_snapshot(
        side_limit=2,
        previous_snapshot=previous,
        current_rows=current_rows,
    )

    assert snapshot["price_source"] == "rest_interval"
    assert snapshot["observation_universe_size"] == 4
    assert [row["symbol"] for row in snapshot["top"]] == ["AAAUSDT", "BBBUSDT"]
    assert [row["symbol"] for row in snapshot["bottom"]] == ["CCCUSDT", "DDDUSDT"]
    assert round(snapshot["top"][0]["change_percent"], 6) == 20.0


def test_binance_market_snapshot_first_sample_has_no_window_ranking():
    snapshot = build_binance_rest_market_snapshot(
        side_limit=2,
        previous_snapshot=None,
        current_rows=[
            {"symbol": "AAAUSDT", "current_price": 1.2},
            {"symbol": "BBBUSDT", "current_price": 2.2},
        ],
    )

    assert snapshot["price_source"] == "rest_interval"
    assert snapshot["top"] == []
    assert snapshot["bottom"] == []
    assert snapshot["sample_count"] == 2


def test_binance_market_state_flags_insufficient_realtime_extremes():
    assert needs_binance_rest_snapshot({"basket": [_row("AVAXUSDT", 100.0, 101.0)]}, side_limit=5)

    truncated = {
        "observation_universe_size": 100,
        "top": [_row(f"T{i}USDT", 100.0, 101.0) for i in range(5)],
        "bottom": [_row(f"B{i}USDT", 100.0, 99.0) for i in range(5)],
    }
    assert needs_binance_rest_snapshot(truncated, side_limit=5)

    enough = {
        "observation_universe_size": 10,
        "top": [_row(f"T{i}USDT", 100.0, 101.0) for i in range(5)],
        "bottom": [_row(f"B{i}USDT", 100.0, 99.0) for i in range(5)],
        "basket": [
            *[_row(f"T{i}USDT", 100.0, 101.0) for i in range(5)],
            *[_row(f"B{i}USDT", 100.0, 99.0) for i in range(5)],
        ],
    }
    assert not needs_binance_rest_snapshot(enough, side_limit=5)


def test_select_binance_market_extremes_refreshes_cached_snapshot(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "binance_market_snapshot.json"
    snapshot = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "top": [
            {"symbol": f"A{i}USDT", "change_percent": 10 - i, "start_price": 1, "current_price": 1 + i}
            for i in range(5)
        ],
        "bottom": [
            {"symbol": f"B{i}USDT", "change_percent": -10 + i, "start_price": 2, "current_price": 2 + i}
            for i in range(5)
        ],
        "basket": [
            *[
                {"symbol": f"A{i}USDT", "change_percent": 10 - i, "start_price": 1, "current_price": 1 + i}
                for i in range(5)
            ],
            *[
                {"symbol": f"B{i}USDT", "change_percent": -10 + i, "start_price": 2, "current_price": 2 + i}
                for i in range(5)
            ],
        ],
        "observation_universe_size": 10,
        "sample_count": 10,
        "price_source": "rest_interval",
    }
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    selected = select_binance_market_extremes(
        {"basket": [_row("AVAXUSDT", 100.0, 101.0)]},
        side_limit=5,
        snapshot_path=snapshot_path,
        current_rows=[
            {"symbol": "A0USDT", "current_price": 1.2},
            {"symbol": "A1USDT", "current_price": 2.2},
            {"symbol": "A2USDT", "current_price": 3.2},
            {"symbol": "A3USDT", "current_price": 4.2},
            {"symbol": "A4USDT", "current_price": 5.2},
            {"symbol": "B0USDT", "current_price": 1.8},
            {"symbol": "B1USDT", "current_price": 2.8},
            {"symbol": "B2USDT", "current_price": 3.8},
            {"symbol": "B3USDT", "current_price": 4.8},
            {"symbol": "B4USDT", "current_price": 5.8},
        ],
    )

    assert selected["market_state_source"] == "rest_interval_live"
    assert [row["symbol"] for row in selected["top"]][:2] == ["A0USDT", "A1USDT"]
    assert [row["symbol"] for row in selected["bottom"]][:2] == ["B0USDT", "B1USDT"]
    assert read_binance_market_snapshot(snapshot_path)["price_source"] == "rest_interval"


def test_select_binance_market_extremes_ignores_24h_rankings(tmp_path):
    snapshot_path = tmp_path / "binance_market_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "basket": [
                    {"symbol": "AAAUSDT", "current_price": 10.0},
                    {"symbol": "BBBUSDT", "current_price": 10.0},
                ],
                "price_source": "rest_interval",
            }
        ),
        encoding="utf-8",
    )
    realtime_24h = {
        "price_source": "binance_rest_24h",
        "top": [_row("PUMPUSDT", 1.0, 2.0)],
        "bottom": [_row("DUMPUSDT", 2.0, 1.0)],
        "basket": [_row("PUMPUSDT", 1.0, 2.0), _row("DUMPUSDT", 2.0, 1.0)],
    }

    selected = select_binance_market_extremes(
        realtime_24h,
        side_limit=1,
        snapshot_path=snapshot_path,
        current_rows=[
            {"symbol": "AAAUSDT", "current_price": 10.5},
            {"symbol": "BBBUSDT", "current_price": 9.5},
        ],
    )

    assert selected["price_source"] == "rest_interval"
    assert selected["market_state_source"] == "rest_interval_live"
    assert [row["symbol"] for row in selected["top"]] == ["AAAUSDT"]
    assert [row["symbol"] for row in selected["bottom"]] == ["BBBUSDT"]


def test_cow_quote_verification_quotes_selected_pairs(monkeypatch):
    monkeypatch.setenv("COW_NETWORK", "avalanche")
    monkeypatch.setenv("COW_CHAIN_ID", "43114")
    monkeypatch.setenv("COW_OWNER_SEPOLIA", "0x" + "9" * 40)
    monkeypatch.setenv("COW_OWNER_AVALANCHE", "0x" + "8" * 40)
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {"rank": 1, "pair": "AAA / BBB", "x_base_symbol": "AAA", "y_base_symbol": "BBB"},
            {"rank": 2, "pair": "CCC / DDD", "x_base_symbol": "CCC", "y_base_symbol": "DDD"},
        ],
    }

    registry = {
        symbol: CowToken(symbol, "0x" + str(index) * 40, 18, "test")
        for index, symbol in enumerate(["USDC", "AAA", "BBB", "CCC", "DDD"], start=1)
    }
    monkeypatch.setattr("market.binance_market.service.build_token_registry", lambda *args, **kwargs: registry)

    quoted_paths = []
    quoted_networks = []
    quoted_owners = []

    def fake_evaluate(route, **kwargs):
        path = route["path"]
        quoted_paths.append(path)
        quoted_networks.append(kwargs["cow_network"])
        quoted_owners.append(kwargs["owner"])
        return {
            "name": route["name"],
            "path": path,
            "input_amount": "1000",
            "input_symbol": path[0],
            "final_symbol": path[-1],
            "final_amount_units": "1200" if path[1] == "BBB" else "900",
            "final_amount": "1200" if path[1] == "BBB" else "900",
            "viable": path[1] == "BBB",
            "hops": [],
        }

    monkeypatch.setattr("market.binance_market.service.evaluate_cow_route", fake_evaluate)
    monkeypatch.setattr(
        "market.binance_market.service.rank_cow_routes",
        lambda items: sorted(items, key=lambda item: int(item["final_amount_units"]), reverse=True),
    )

    payload = build_cow_quote_verification(market_state, quote_limit=2)

    assert payload["selected_pair_count"] == 2
    assert payload["route_count"] == 4
    assert payload["viable_count"] == 1
    assert payload["cow_network"] == "avalanche"
    assert payload["cow_chain_id"] == 43114
    assert payload["cow_testnet"] is False
    assert payload["owner"] == "0x" + "8" * 40
    assert payload["owner_source"] == "COW_OWNER_AVALANCHE"
    assert quoted_paths[0] == ["USDC", "BBB", "AAA", "USDC"]
    assert quoted_paths[1] == ["USDC", "AAA", "BBB", "USDC"]
    assert len(quoted_paths) == 4
    assert set(quoted_networks) == {"avalanche"}
    assert set(quoted_owners) == {"0x" + "8" * 40}
    assert payload["best"]["path"][1] == "BBB"
    assert payload["best"]["priority_reason"] == "buy_loser_then_gainer"
    assert payload["best"]["costs"]["quote_api_gas_used"] == 0
    assert payload["best"]["costs"]["settlement_gas_payer"] == "solver"
    assert payload["best"]["costs"]["approval_gas_status"] == "requires_allowance_check_before_execution"
    assert payload["best"]["costs"]["profit_amount"] is None
    assert payload["best"]["costs"]["profit_prediction_disabled"] is True


def test_cow_quote_verification_records_all_chain_candidates(monkeypatch):
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {"rank": 1, "pair": "AAA / BBB", "x_base_symbol": "AAA", "y_base_symbol": "BBB"},
            {"rank": 2, "pair": "CCC / DDD", "x_base_symbol": "CCC", "y_base_symbol": "DDD"},
            {"rank": 3, "pair": "EEE / FFF", "x_base_symbol": "EEE", "y_base_symbol": "FFF"},
        ],
    }
    registry = {
        symbol: CowToken(symbol, "0x" + f"{index:040d}"[-40:], 18, "test")
        for index, symbol in enumerate(["USDC", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF"], start=1)
    }
    monkeypatch.setattr("market.binance_market.service.build_token_registry", lambda *args, **kwargs: registry)
    monkeypatch.setattr(
        "market.binance_market.service.evaluate_cow_route",
        lambda route, **kwargs: {
            "name": route["name"],
            "path": route["path"],
            "input_amount": "1000",
            "input_symbol": "USDC",
            "final_symbol": "USDC",
            "final_amount_units": "1000500000000000000000",
            "final_amount": "1000.5",
            "viable": True,
            "hops": [{"hop": 1, "sell_symbol": "USDC", "buy_symbol": route["path"][1], "sell_amount": "1000", "buy_amount": "1000"}],
        },
    )
    monkeypatch.setattr("market.binance_market.service.rank_cow_routes", lambda items: items)

    payload = build_cow_quote_verification(market_state, quote_limit=1, registry=registry)
    attempts = build_cow_execution_attempts(payload, market_state=market_state)

    assert payload["selected_pair_count"] == 1
    assert payload["route_count"] == 2
    assert len(attempts) == 2
    assert {attempt["pair"] for attempt in attempts} == {"AAA / BBB"}


def test_cow_quote_verification_selects_target_and_slippage_from_three_prices(monkeypatch):
    market_state = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "pairs": [
            {
                "rank": 1,
                "pair": "AAA / BBB",
                "x_base_symbol": "AAA",
                "y_base_symbol": "BBB",
                "x_start_price": 10,
                "x_current_price": 12,
                "y_start_price": 9,
                "y_current_price": 10,
            },
        ],
    }
    registry = {
        symbol: CowToken(symbol, "0x" + str(index) * 40, 18, "test")
        for index, symbol in enumerate(["USDC", "AAA", "BBB"], start=1)
    }

    def fake_evaluate(route, **kwargs):
        path = route["path"]
        if path == ["USDC", "BBB", "AAA", "USDC"]:
            hops = [
                {
                    "hop": 1,
                    "sell_symbol": "USDC",
                    "buy_symbol": "BBB",
                    "sell_amount": "1000",
                    "buy_amount": "125",
                    "exchange_rate": "0.125",
                    "fee_amount": "0",
                },
                {
                    "hop": 2,
                    "sell_symbol": "BBB",
                    "buy_symbol": "AAA",
                    "sell_amount": "125",
                    "buy_amount": "137.5",
                    "exchange_rate": "1.1",
                    "fee_amount": "0",
                },
                {
                    "hop": 3,
                    "sell_symbol": "AAA",
                    "buy_symbol": "USDC",
                    "sell_amount": "137.5",
                    "buy_amount": "1512.5",
                    "exchange_rate": "11",
                    "fee_amount": "0",
                },
            ]
            return {
                "name": route["name"],
                "path": path,
                "input_amount": "1000",
                "input_symbol": "USDC",
                "final_symbol": "USDC",
                "final_amount_units": "1512",
                "final_amount": "1512.5",
                "viable": True,
                "hops": hops,
            }
        return {
            "name": route["name"],
            "path": path,
            "input_amount": "1000",
            "input_symbol": "USDC",
            "final_symbol": "USDC",
            "final_amount_units": "0",
            "final_amount": "0",
            "viable": False,
            "hops": [],
        }

    monkeypatch.setattr("market.binance_market.service.evaluate_cow_route", fake_evaluate)
    monkeypatch.setattr("market.binance_market.service.rank_cow_routes", lambda items: items)
    monkeypatch.setattr("market.binance_market.service._cow_order_submission_adapter_available", lambda: False)

    payload = build_cow_quote_verification(market_state, quote_limit=1, registry=registry)

    plan = payload["ranking"][0]["binance_execution_plan"]
    buy_step, middle_step, sell_step = plan["steps"]
    assert buy_step["price_candidates"] == [
        {"source": "previous_window", "price": "9"},
        {"source": "current_window", "price": "10"},
        {"source": "query_quote", "price": "8"},
    ]
    assert buy_step["selected_target_source"] == "previous_window"
    assert buy_step["selected_target_price_usd_per_token"] == "9"
    assert buy_step["acceptable_slippage_price_usd_per_token"] == "10"
    assert buy_step["query_price_position"]["position"] == "lowest"
    assert buy_step["query_guard_analysis"]["status"] == "better_than_target"
    assert buy_step["cow_sdk_parameters"]["sell_amount_before_fee"] == "1000"
    expected_target_buy = Decimal("111.1111111111111111111111111")
    assert Decimal(buy_step["cow_sdk_parameters"]["target_buy_amount_after_fee"]).quantize(expected_target_buy) == expected_target_buy
    assert buy_step["cow_sdk_parameters"]["min_buy_amount_after_fee"] == "100"

    assert middle_step["selected_target_source"] == "previous_window"
    assert middle_step["selected_target_exchange_rate"] == "0.9"
    assert middle_step["acceptable_slippage_exchange_rate"] == "0.9"
    assert middle_step["query_rate_position"]["position"] == "highest"
    assert middle_step["query_guard_analysis"]["status"] == "better_than_target"
    assert middle_step["cow_sdk_parameters"]["target_buy_amount_after_fee"] == "90"
    assert middle_step["cow_sdk_parameters"]["min_buy_amount_after_fee"] == "90"

    assert sell_step["selected_target_source"] == "current_window"
    assert sell_step["selected_target_price_usd_per_token"] == "12"
    assert sell_step["acceptable_slippage_price_usd_per_token"] == "12"
    assert sell_step["query_price_position"]["position"] == "middle"
    assert sell_step["query_guard_analysis"]["status"] == "worse_than_guard"
    assert sell_step["cow_sdk_parameters"]["target_buy_amount_after_fee"] == "1080"
    assert sell_step["cow_sdk_parameters"]["min_buy_amount_after_fee"] == "1080"
    precheck = payload["ranking"][0]["execution_precheck"]
    sdk_plan = precheck["cow_flashloan_sdk_plan"]
    assert sdk_plan["single_solver_order_count"] == 1
    assert sdk_plan["diagnostic_hop_count"] == 3
    assert precheck["status"] in {"quote_not_verified", "order_submission_switch_off", "order_submission_adapter_unavailable"}
    assert precheck["checks_passed"] is True
    assert precheck["can_submit_order"] is False
    assert precheck["quote_verified"] is False
    assert precheck["price_guards_passed"] is False
    assert precheck["profit_prediction_disabled"] is True
    assert precheck["quote_delta_positive"] is None
    assert precheck["flashloan_capability"]["multi_step_route"] is True
    assert precheck["flashloan_capability"]["supports_multi_step_atomic_settlement"] is True
    assert precheck["flashloan_capability"]["quote_probe_reliability"]["per_hop_quotes_are_not_atomicity_proof"] is True
    assert precheck["flashloan_capability"]["requires_custom_contract_deployment"] is False
    assert precheck["flashloan_capability"]["pending_fields"] == []
    assert precheck["flashloan_capability"]["intent_order"]["loan_model"] == "sdk_managed_flashloan_no_user_loan_array"
    assert precheck["flashloan_capability"]["intent_order"]["flashloan_router_call_count"] == 0
    assert precheck["flashloan_capability"]["intent_order"]["independent_per_hop_orders"] == 0
    assert precheck["flashloan_capability"]["intent_order"]["solver_intermediate_symbols"] == ["BBB", "AAA"]
    assert precheck["flashloan_capability"]["intent_order"]["closed_cycle"] is True
    assert precheck["flashloan_capability"]["intent_order"]["order"]["sell_token_symbol"] == "USDC"
    assert precheck["cow_flashloan_sdk_plan"]["required_submission_method"].startswith("TradingSdk.getQuote")
    assert [item["status"] for item in precheck["hop_checks"]] == ["pass", "pass", "pass"]
    assert precheck["hop_checks"][0]["min_buy_amount_after_fee"] == "100"
    assert payload["opportunity_count"] == 2
    assert payload["best_opportunity"]["pair"] == "AAA / BBB"
    assert payload["best"]["execution_precheck"]["checks_passed"] is True


def test_cow_order_submission_enabled_when_page_switch_is_on(monkeypatch):
    monkeypatch.delenv("COW_ORDER_SUBMISSION_ENABLED", raising=False)
    monkeypatch.setattr(
         "dex.runtime.cow_submission_pause.cow_submission_pause_guard_status",
        lambda: {
            "configured": True,
            "database_configured": True,
            "source": "database",
            "paused": False,
            "order_submission_enabled": True,
            "pause_reason": None,
        },
    )

    assert _cow_order_submission_enabled() is True


def test_cow_order_submission_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.delenv("COW_ORDER_SUBMISSION_ENABLED", raising=False)
    monkeypatch.setenv("COW_ORDER_SUBMISSION_ADAPTER_ENABLED", "false")
    monkeypatch.setattr(
         "dex.runtime.cow_submission_pause.cow_submission_pause_guard_status",
        lambda: {
            "configured": True,
            "database_configured": True,
            "source": "database",
            "paused": False,
            "order_submission_enabled": True,
            "pause_reason": None,
        },
    )

    assert _cow_order_submission_enabled() is False


def test_cow_quote_unavailable_uses_quote_error_as_primary_blocker(monkeypatch, tmp_path):
    cloudfront_403 = """
    <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
    <HTML><HEAD><TITLE>ERROR: The request could not be satisfied</TITLE></HEAD>
    <BODY><H1>403 ERROR</H1><H2>The request could not be satisfied.</H2>
    Request blocked. Generated by cloudfront (CloudFront)</BODY></HTML>
    """
    market_state = {
        "observed_at": "2026-08-05T13:45:37+00:00",
        "window_seconds": 118.613,
        "price_source": "rest_interval",
        "pairs": [
            {
                "rank": 1,
                "pair": "SPCXBUSDT / SOXSBUSDT",
                "x_symbol": "SPCXBUSDT",
                "y_symbol": "SOXSBUSDT",
                "x_base_symbol": "SPCXB",
                "y_base_symbol": "SOXSB",
                "x_start_price": 110.9,
                "x_current_price": 112.77,
                "x_change_percent": 0,
                "y_start_price": 43.87,
                "y_current_price": 43.65,
                "y_change_percent": 0,
            }
        ],
    }
    registry = {
        symbol: CowToken(symbol, "0x" + str(index) * 40, 18, "test")
        for index, symbol in enumerate(["USDC", "SPCXB", "SOXSB"], start=1)
    }
    monkeypatch.setattr(
        "market.binance_market.service.evaluate_cow_route",
        lambda route, **kwargs: {
            "name": route["name"],
            "path": route["path"],
            "input_amount": "1000",
            "input_symbol": "USDC",
            "final_symbol": "USDC",
            "viable": False,
            "error": cloudfront_403,
            "hops": [],
        },
    )

    payload = build_cow_quote_verification(market_state, quote_limit=1, registry=registry)
    precheck = payload["ranking"][0]["execution_precheck"]

    assert precheck["status"] in {
        "quote_not_verified",
        "limit_order_ready_to_submit",
        "order_submission_switch_off",
        "order_submission_adapter_unavailable",
        "order_submission_signer_not_ready",
    }
    assert precheck["checks_passed"] is True
    assert precheck["can_submit_order"] is False
    assert precheck["quote_available"] is False
    assert precheck["quote_verified"] is False
    assert precheck["own_limit_order_ready"] is True
    assert precheck["quote_error_type"] == "quote_api_http_403_cloudfront_request_blocked"
    assert precheck["quote_error_type"] == "quote_api_http_403_cloudfront_request_blocked"
    assert "cow_flashloan_sdk_intent_ready" in precheck["reasons"]
    assert not any("actual_output_below_own_minimum" in reason for reason in precheck["reasons"])
    assert set(item["failure_cause"] for item in precheck["hop_checks"]) == {"quote_api_http_403_cloudfront_request_blocked"}
    assert set(item["status"] for item in precheck["hop_checks"]) == {"not_checked"}

    attempts = build_cow_execution_attempts(payload, market_state=market_state)
    path = tmp_path / "cow_execution_attempts.jsonl"
    assert append_cow_execution_attempts_jsonl(path, attempts) == 2
    rows = load_recent_cow_execution_attempts_jsonl(path, limit=10)
    assert {row["priority_reason"] for row in rows} == {"buy_loser_then_gainer", "reverse_check"}
    summary = rows[0]["review_summary"]
    assert summary["cow_quote"]["error_type"] == "quote_api_http_403_cloudfront_request_blocked"
    assert summary["cow_quote"]["error"] == "CoW quote API HTTP 403: CloudFront request blocked"
    assert summary["market_prices"][0]["change_percent"] != 0
    assert summary["market_prices"][1]["change_percent"] != 0


