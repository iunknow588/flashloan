"""Tests for directional closed-loop identity helpers."""

from __future__ import annotations

from dex.runtime.closed_loop_identity import (
    directed_symbol_key_from_symbols,
    normalize_physical_loop_key,
    physical_loop_key_from_addresses,
    physical_loop_key_from_route,
    worker_group_key_from_route,
    worker_group_key_from_symbols,
)


ANCHOR = "0x" + ("1" * 40)
Y = "0x" + ("2" * 40)
Z = "0x" + ("3" * 40)


def test_physical_and_worker_keys_preserve_direction_boundaries() -> None:
    forward = [ANCHOR, Y, Z, ANCHOR]
    reverse = [ANCHOR, Z, Y, ANCHOR]

    assert physical_loop_key_from_addresses(forward) == f"{ANCHOR}:{Y}:{Z}"
    assert physical_loop_key_from_addresses(reverse) == f"{ANCHOR}:{Z}:{Y}"
    assert directed_symbol_key_from_symbols(["SPY", "TSLA", "USDG", "SPY"]) == (
        "spy|tsla|usdg"
    )
    assert directed_symbol_key_from_symbols(["SPY", "USDG", "TSLA", "SPY"]) == (
        "spy|usdg|tsla"
    )
    assert worker_group_key_from_symbols(["SPY", "TSLA", "USDG", "SPY"]) == (
        "spy|tsla|usdg"
    )
    assert worker_group_key_from_symbols(["SPY", "USDG", "TSLA", "SPY"]) == (
        "spy|tsla|usdg"
    )


def test_physical_loop_key_rejects_invalid_or_non_closed_paths() -> None:
    assert physical_loop_key_from_addresses([ANCHOR, Y]) is None
    assert physical_loop_key_from_addresses([ANCHOR, Y, Z, "0xdead"]) is None
    assert physical_loop_key_from_addresses([ANCHOR, Y, Y, ANCHOR]) is None
    assert directed_symbol_key_from_symbols(["SPY", "TSLA", "USDG"]) == (
        "spy|tsla|usdg"
    )
    assert directed_symbol_key_from_symbols(["SPY", "TSLA"]) is None
    assert worker_group_key_from_symbols("SPY|TSLA|USDG") is None


def test_normalize_physical_loop_key_accepts_three_part_key_only() -> None:
    assert normalize_physical_loop_key(f"{ANCHOR.upper()}|{Y.upper()}|{Z.upper()}") == (
        f"{ANCHOR}:{Y}:{Z}"
    )
    assert normalize_physical_loop_key(f"{ANCHOR}:{Y}:{Z}:{ANCHOR}") == (
        f"{ANCHOR}:{Y}:{Z}"
    )
    assert normalize_physical_loop_key("route:v1:spy|tsla|usdg") is None
    assert normalize_physical_loop_key(f"{ANCHOR}|{Y}") is None
    assert normalize_physical_loop_key("spy|tsla|usdg") is None
    assert normalize_physical_loop_key(f"{ANCHOR}|{Y}|not-an-address") is None


def test_route_identity_prefers_physical_key_then_trusted_addresses() -> None:
    route = {
        "loopKey": f"{ANCHOR}|{Y}|{Z}",
        "routeAddresses": [ANCHOR, Z, Y, ANCHOR],
        "routeSymbols": ["SPY", "USDG", "TSLA", "SPY"],
    }

    assert physical_loop_key_from_route(route) == f"{ANCHOR}:{Y}:{Z}"
    assert worker_group_key_from_route(route) == "spy|tsla|usdg"

    route["loopKey"] = "route:v1:" + str(route["loopKey"])
    assert physical_loop_key_from_route(route) == f"{ANCHOR}:{Z}:{Y}"


def test_route_identity_rejects_untrusted_or_malformed_paths() -> None:
    assert physical_loop_key_from_route(None) is None
    assert physical_loop_key_from_route({"routeAddresses": [ANCHOR, Y, Z, "0xdead"]}) is None
    assert physical_loop_key_from_route(
        {
            "loopKey": "route:v1:spy|tsla|usdg",
            "routeAddresses": [ANCHOR, Y, Z, ANCHOR],
        }
    ) == f"{ANCHOR}:{Y}:{Z}"
