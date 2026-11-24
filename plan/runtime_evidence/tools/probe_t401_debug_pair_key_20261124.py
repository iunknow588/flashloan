#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Debug why a synthetic directed route is not grouped by the scanner."""

import os
import sys

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "flashloan", "src"
    ),
)
sys.stdout.reconfigure(encoding="utf-8")

from dex.runtime.closed_loop_identity import (  # noqa: E402
    physical_loop_key_from_addresses,
    worker_group_key_from_route,
)
from dex.runtime.dex_opportunity_route_runner import _route_is_closed_loop_quad  # noqa: E402


def main():
    index = 0
    symbols = [f"T{index}A", f"T{index}B", f"T{index}C"]
    addresses = []
    for position, symbol in enumerate(symbols):
        addresses.append("0x" + (f"{index:02x}{position:x}" + "0" * 36))
    route = {
        "routeSymbols": symbols + [symbols[0]],
        "routeAddresses": addresses + [addresses[0]],
    }
    print("quad?", _route_is_closed_loop_quad(route))
    print("physical", physical_loop_key_from_addresses(addresses))
    print("group", worker_group_key_from_route(route))
    print("address lens", [len(a) for a in addresses])


if __name__ == "__main__":
    main()
