"""Cover the 2026-11-01 trust boundaries for route symbols and best amount."""

from __future__ import annotations

import pytest

from core.db.cow_execution_review import _review_route_identity
from dex.runtime import closed_loop_review_classifier as classifier
from dex.runtime.dex_opportunity_review_engine_fixed import (
    DexOpportunityFixedRefreshMixin,
)
from market.binance_market.dex_route_helpers import (
    _official_route_addresses,
    _route_addresses_from_pool_keys,
    normalize_route_symbols_from_addresses,
    trusted_symbol_from_address,
)


def _addresses(symbols: list[str], chain_id: int = 4663) -> list[str]:
    addresses = _official_route_addresses(symbols, chain_id=chain_id)
    assert len(addresses) == len(symbols)
    return addresses


def test_normalize_resolves_known_placeholder_from_mainnet_allowlist() -> None:
    addresses = _addresses(["WETH", "SPY", "USDG", "WETH"])

    normalized = normalize_route_symbols_from_addresses(
        route_symbols=["WETH", "SPY", "UNRESOLVED:0X5FC5360D", "WETH"],
        route_addresses=addresses,
        chain_id=4663,
    )

    assert normalized == ["WETH", "SPY", "USDG", "WETH"]


def test_mainnet_stock_identity_uses_sqlite_registry_not_json(monkeypatch, tmp_path) -> None:
    import core.db.database_path as database_path
    from market.binance_market import dex_route_helpers as helpers

    stock_address = "0x" + "1" * 40
    captured: dict[str, object] = {}

    def fake_stock_token_by_symbol(**kwargs):
        captured.update(kwargs)
        assert kwargs.get("database_url")
        return {"SPY": {"address": stock_address, "symbol": "SPY"}}

    previous_cache = helpers._SYMBOL_ADDRESS_CACHE.pop(4663, None)
    monkeypatch.setattr(helpers, "stock_token_by_symbol", fake_stock_token_by_symbol)
    monkeypatch.setattr(
        database_path,
        "absolute_database_path",
        lambda database_url=None: tmp_path / "mainnet.sqlite3",
    )
    try:
        assert helpers._official_route_addresses(["SPY"], chain_id=4663) == [stock_address]
        assert captured["network"] == "robinhood"
        assert captured["chain_id"] == 4663
        assert str(captured["database_url"]).endswith("mainnet.sqlite3")
    finally:
        if previous_cache is None:
            helpers._SYMBOL_ADDRESS_CACHE.pop(4663, None)
        else:
            helpers._SYMBOL_ADDRESS_CACHE[4663] = previous_cache


def test_trusted_symbol_lookup_rejects_unknown_address_even_if_it_claims_known_name() -> None:
    unknown = "0x" + "d" * 40

    assert trusted_symbol_from_address(unknown, chain_id=4663) is None


def test_loop_key_from_review_row_uses_addresses_not_spoofable_symbols() -> None:
    addresses = _addresses(["WETH", "SPY", "USDG", "WETH"])
    spoofed = {
        "chain_id": 4663,
        "routeSymbols": ["WETH", "EVIL", "USDG", "WETH"],
        "routeAddresses": addresses,
    }

    assert classifier.loop_key_from_review_row(spoofed) == ":".join(
        address.lower() for address in addresses[:3]
    )

    unknown = {
        "chain_id": 4663,
        "routeSymbols": ["WETH", "EVIL", "NVDA", "WETH"],
        "routeAddresses": [
            addresses[0],
            "0x" + "e" * 40,
            _addresses(["NVDA"])[0],
            addresses[0],
        ],
    }
    unknown_key = classifier.loop_key_from_review_row(unknown)
    assert unknown_key == ":".join(
        [addresses[0].lower(), ("0x" + "e" * 40), _addresses(["NVDA"])[0].lower()]
    )


def test_loop_key_from_review_row_preserves_reverse_direction() -> None:
    forward = _addresses(["WETH", "SPY", "USDG", "WETH"])
    reverse = [forward[0], forward[2], forward[1], forward[0]]
    row = {
        "chain_id": 4663,
        "routeSymbols": ["WETH", "USDG", "SPY", "WETH"],
        "routeAddresses": reverse,
    }

    assert classifier.loop_key_from_review_row(row) == ":".join(
        address.lower() for address in reverse[:3]
    )


def test_review_route_identity_reads_and_normalizes_placeholder_without_writing() -> None:
    addresses = _addresses(["WETH", "SPY", "USDG", "WETH"])
    row = {
        "chain_id": 4663,
        "pair": "WETH -> SPY -> UNRESOLVED:0X5FC5360D -> WETH",
    }
    quote = {"routeAddresses": addresses}
    live_evidence = {
        "routeSymbols": ["WETH", "SPY", "UNRESOLVED:0X5FC5360D", "WETH"],
    }

    identity = _review_route_identity(row, quote, live_evidence)

    assert identity["route_symbols"] == ["WETH", "SPY", "USDG", "WETH"]
    assert identity["route_addresses"] == addresses
    assert row.get("pair") == "WETH -> SPY -> UNRESOLVED:0X5FC5360D -> WETH"


def test_pool_key_recovery_uses_real_addresses_for_unknown_intermediate() -> None:
    weth = _addresses(["WETH"])[0]
    nvda = _addresses(["NVDA"])[0]
    mystery = "0x" + "a" * 40
    route = {
        "routeSymbols": ["WETH", "MYSTERY", "NVDA", "WETH"],
        "routeAddresses": [],
        "routePools": [
            {
                "poolKey": {
                    "currency0": weth,
                    "currency1": mystery,
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                }
            },
            {
                "poolKey": {
                    "currency0": mystery,
                    "currency1": nvda,
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                }
            },
            {
                "poolKey": {
                    "currency0": weth,
                    "currency1": nvda,
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": "0x0000000000000000000000000000000000000000",
                }
            },
        ],
    }

    addresses = _route_addresses_from_pool_keys(
        route,
        ["WETH", "MYSTERY", "NVDA", "WETH"],
        chain_id=4663,
    )

    assert [address.lower() for address in addresses] == [
        weth.lower(),
        mystery.lower(),
        nvda.lower(),
        weth.lower(),
    ]


def test_fixed_refresh_attempt_writes_trusted_pair_and_no_unconfirmed_best_amount() -> None:
    addresses = _addresses(["WETH", "SPY", "USDG", "WETH"])
    route = {
        "routeSymbols": ["WETH", "SPY", "UNRESOLVED:0X5FC5360D", "WETH"],
        "routeAddresses": addresses,
        "__liveEstimate": {
            "quoteStatus": "live_quote_failed",
            "executionTrust": "review_failed",
            "ok": False,
            "detail": "test",
            "liveVerified": False,
            "profitable": False,
            "bestAmountConfirmed": False,
            "bestAmountMissing": True,
            "amount": 1.0,
            "steps": [],
            "evidence": {
                "costModel": {
                    "borrowInputAmount": 1.0,
                    "requestedBorrowInputAmount": 1.0,
                    "selectedBorrowInputAmount": 1.0,
                    "bestAmountConfirmed": False,
                    "bestAmountMissing": True,
                }
            },
        },
    }
    engine = DexOpportunityFixedRefreshMixin.__new__(
        DexOpportunityFixedRefreshMixin
    )

    attempt = engine._fixed_refresh_attempt(
        route,
        chain_id=4663,
        observed_at="2026-11-01T00:00:00Z",
    )

    assert attempt is not None
    assert attempt["pair"] == "WETH -> SPY -> USDG -> WETH"
    assert attempt["route_path"] == ["WETH", "SPY", "USDG", "WETH"]
    costs = attempt["quote"]["live_onchain_evidence"]["costModel"]
    assert costs["selectedBorrowInputAmount"] is None
    assert costs["bestAmountMissing"] is True
