from __future__ import annotations

from market.binance_market.dex_route_test import (
    _official_route_addresses,
    _quote_failure_status,
    _step_failure_status,
    is_official_triangle_route,
    normalize_route_symbols,
    recommended_route_test_amount,
    run_official_v4_arb_route_test,
)


def test_normalize_route_symbols_supports_arrow_and_comma_text():
    assert normalize_route_symbols("TZ -> TB -> TA -> TZ") == ["TZ", "TB", "TA", "TZ"]
    assert normalize_route_symbols("ta,tb,tz,ta") == ["TA", "TB", "TZ", "TA"]


def test_official_triangle_route_validation():
    assert is_official_triangle_route(["TA", "TB", "TZ", "TA"])
    assert not is_official_triangle_route(["WETH", "ETH", "0XC9F9C8", "WETH"])
    assert not is_official_triangle_route(["TZ", "TB", "TA"])


def test_official_route_addresses_resolve_from_chain_allowlist():
    addresses = _official_route_addresses(["WETH", "ETH", "0XC9F9C8", "WETH"])
    assert addresses == [
        "0x7943e237c7F95DA44E0301572D358911207852Fa",
        "0x0000000000000000000000000000000000000000",
        "0xc9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E",
        "0x7943e237c7F95DA44E0301572D358911207852Fa",
    ]


def test_official_route_addresses_rejects_unknown_symbol_instead_of_guessing():
    assert _official_route_addresses(["WETH", "UNKNOWN_BAD_TOKEN", "WETH"]) == []


def test_unsupported_route_returns_friendly_blocked_result():
    result = run_official_v4_arb_route_test(
        route_symbols=["WETH", "ETH", "0XC9F9C8", "WETH"],
        amount=1,
        broadcast=False,
    )
    assert result["ok"] is False
    assert result["error"] == "official_arb_broadcast_route_unsupported"
    assert result["routeSymbols"] == ["WETH", "ETH", "0XC9F9C8", "WETH"]


def test_recommended_route_test_amount_uses_size_analysis(monkeypatch):
    import market.binance_market.dex_route_test as route_test_module

    monkeypatch.setattr(
        route_test_module,
        "_routes_payload",
        lambda **kwargs: {
            "routes": [
                {
                    "routeSymbols": ["TZ", "TB", "TA", "TZ"],
                    "sizeAnalysis": {
                        "recommendedBorrowInputAmount": 12.5,
                        "tokenAmountSteps": [0.01, 0.1, 1.0, 10.0, 100.0],
                    },
                }
            ]
        },
    )
    amount = recommended_route_test_amount(
        route_symbols=["TZ", "TB", "TA", "TZ"],
        chain_id=46630,
        protocols=["uniswap_v4"],
    )
    assert amount == 12.5


def test_recommended_route_test_amount_falls_back_to_token_steps(monkeypatch):
    import market.binance_market.dex_route_test as route_test_module

    monkeypatch.setattr(
        route_test_module,
        "_routes_payload",
        lambda **kwargs: {
            "routes": [
                {
                    "routeSymbols": ["TZ", "TB", "TZ"],
                    "sizeAnalysis": {
                        "tokenAmountSteps": [0.01, 0.1, 1.0, 10.0, 100.0],
                    },
                }
            ]
        },
    )
    amount = recommended_route_test_amount(
        route_symbols=["TZ", "TB", "TZ"],
        chain_id=46630,
        protocols=["uniswap_v4"],
    )
    assert amount == 0.01


def test_quote_failure_status_detects_liquidity_zero():
    assert _quote_failure_status("robinhood testnet v4 pool manager live liquidity is zero") == "not_enough_liquidity"
    assert _quote_failure_status("robinhood testnet v4 quoter reverted: NotEnoughLiquidity(bytes32)") == "not_enough_liquidity"
    assert _quote_failure_status("rpc connection refused") == "live_quote_failed"


def test_step_failure_status_upgrades_liquidity_error():
    step = {
        "executionTrust": "live_quote_failed",
        "liveQuoteError": "robinhood testnet v4 pool manager live liquidity is zero",
    }
    assert _step_failure_status(step) == "not_enough_liquidity"
