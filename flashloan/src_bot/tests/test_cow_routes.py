from decimal import Decimal

import pytest

from execution.cow_routes import (
    CowToken,
    evaluate_cow_route,
    from_units,
    parse_route_path,
    rank_cow_routes,
    resolve_token,
    to_units,
)


def test_parse_route_path_accepts_common_notation():
    assert parse_route_path("USDC -> AAVE -> USDC") == ["USDC", "AAVE", "USDC"]
    assert parse_route_path(["USDC", "WAVAX"]) == ["USDC", "WAVAX"]


def test_units_round_trip_flooring():
    assert to_units("1.2345678", 6) == "1234567"
    assert from_units("1234567", 6) == "1.234567"


def test_resolve_token_rejects_ambiguous_or_unknown_symbol():
    registry = {"USDC": CowToken("USDC", "0x" + "1" * 40, 6, "test")}
    assert resolve_token("usdc", registry).symbol == "USDC"
    with pytest.raises(ValueError, match="unknown or ambiguous"):
        resolve_token("ABC", registry)


def test_evaluate_cow_route_ranks_by_final_amount(monkeypatch):
    usdc = CowToken("USDC", "0x" + "1" * 40, 6, "test")
    aave = CowToken("AAVE", "0x" + "2" * 40, 18, "test")
    registry = {"USDC": usdc, "AAVE": aave, usdc.address: usdc, aave.address: aave}

    def fake_quote(**kwargs):
        sell = kwargs["sell_token"].symbol
        buy = kwargs["buy_token"].symbol
        if (sell, buy) == ("USDC", "AAVE"):
            buy_amount = "2000000000000000000"
        else:
            buy_amount = "1100000000"
        return {"quote": {"buyAmount": buy_amount, "sellAmount": kwargs["sell_amount_units"], "feeAmount": "0"}}

    monkeypatch.setattr("execution.cow_routes.post_cow_quote", fake_quote)
    result = evaluate_cow_route(
        {"name": "r1", "path": ["USDC", "AAVE", "USDC"]},
        registry=registry,
        default_amount=Decimal("1000"),
    )
    assert result["viable"] is True
    assert result["final_amount"] == "1100"

    ranked = rank_cow_routes([result, {"name": "bad", "viable": False}])
    assert ranked[0]["name"] == "r1"
