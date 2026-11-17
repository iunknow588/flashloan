"""Tests for directional closed-loop identity helpers."""

from __future__ import annotations

from dex.runtime.closed_loop_identity import (
    directed_symbol_key_from_symbols,
    normalize_physical_loop_key,
    physical_loop_key_from_addresses,
    physical_loop_key_from_route,
    worker_group_key_from_route,
    resolve_registered_group_key,
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
        "spy|usdg|tsla"
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
    assert worker_group_key_from_route(route) == "spy|usdg|tsla"

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


# --- GROUP-KEY-ROTATION ----------------------------------------------------
#
# The closed-loop library stores every loop ETH-anchored ("ETH -> Y -> Z ->
# ETH"), while the scanner registers its resident worker under the route
# order it discovered, which is a rotation of the same directed cycle
# ("Z -> ETH -> Y"). The worker pool keys its state machine by the exact
# registered string, so a manual review addressed by the library order used
# to miss the worker and answer with CLOSED_LOOP_GROUP_NOT_FOUND.


def test_a_rotation_of_the_same_directed_cycle_resolves_to_the_registered_key():
    registered = ["tsla|eth|amzn", "weth|eth|tsla"]

    assert resolve_registered_group_key(
        ["ETH", "AMZN", "TSLA"], registered
    ) == "tsla|eth|amzn"
    assert resolve_registered_group_key(
        ["AMZN", "TSLA", "ETH"], registered
    ) == "tsla|eth|amzn"


def test_the_reverse_direction_never_resolves_to_the_forward_worker():
    registered = ["tsla|eth|amzn"]

    assert resolve_registered_group_key(["ETH", "TSLA", "AMZN"], registered) is None
    assert resolve_registered_group_key(["AMZN", "ETH", "TSLA"], registered) is None


def test_an_unregistered_cycle_resolves_to_nothing():
    assert resolve_registered_group_key(["ETH", "AMD", "AMZN"], []) is None
    assert resolve_registered_group_key(["ETH", "AMD", "AMZN"], ["tsla|eth|amzn"]) is None


def test_resolution_ignores_malformed_request_paths():
    assert resolve_registered_group_key(["ETH", "AMZN"], ["tsla|eth|amzn"]) is None
    assert resolve_registered_group_key([], ["tsla|eth|amzn"]) is None
    assert resolve_registered_group_key(["ETH", "AMZN", "ETH"], ["tsla|eth|amzn"]) is None
