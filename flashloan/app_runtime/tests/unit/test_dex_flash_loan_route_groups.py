from market.binance_market.dex_flash_loan_route_groups import (
    closed_loop_route_groups_payload,
    group_closed_loop_routes_by_flash_loan_token,
)
from market.binance_market.dex_surface_overview import group_overview_paths_by_flash_loan_token


def _pool(pool_id, route_symbols, direction):
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "routeSymbols": route_symbols,
        "direction": direction,
        "dex": "uniswap_v4",
        "fee": "3000",
    }


def _snapshot():
    return {
        "chain_id": 46630,
        "network": "robinhood-testnet",
        "borrowableSymbols": ["TA", "TB"],
        "closedLoopRouteSummaries": [
            {
                "routeKey": "route-ta-a",
                "anchorSymbol": "TA",
                "counterSymbol": "TB",
                "bridgeSymbol": "TZ",
                "pathText": "TA -> TZ -> TB -> TA",
                "pathSymbols": ["TA", "TZ", "TB", "TA"],
                "routeAddresses": ["0xTA", "0xTZ", "0xTB", "0xTA"],
                "pairSymbols": ["TA -> TZ", "TZ:TB", "TB -> TA"],
                "poolIds": ["pool-ta-tz", "pool-tz-tb", "pool-tb-ta"],
                "routePools": [
                    _pool("pool-ta-tz", ["TA", "TZ"], "yc"),
                    _pool("pool-tz-tb", ["TZ", "TB"], "yc"),
                    _pool("pool-tb-ta", ["TB", "TA"], "zc"),
                ],
            },
            {
                "routeKey": "route-ta-b",
                "anchorSymbol": "TA",
                "counterSymbol": "TZ",
                "bridgeSymbol": "TB",
                "pathText": "TA -> TB -> TZ -> TA",
                "pathSymbols": ["TA", "TB", "TZ", "TA"],
                "routeAddresses": ["0xTA", "0xTB", "0xTZ", "0xTA"],
                "pairSymbols": ["TA -> TB", "TB:TZ", "TZ -> TA"],
                "poolIds": ["pool-ta-tb", "pool-tb-tz", "pool-tz-ta"],
                "routePools": [
                    _pool("pool-ta-tb", ["TA", "TB"], "yc"),
                    _pool("pool-tb-tz", ["TB", "TZ"], "yc"),
                    _pool("pool-tz-ta", ["TZ", "TA"], "zc"),
                ],
            },
            {
                "routeKey": "route-tb",
                "anchorSymbol": "TB",
                "counterSymbol": "TA",
                "bridgeSymbol": "TZ",
                "pathText": "TB -> TZ -> TA -> TB",
                "pathSymbols": ["TB", "TZ", "TA", "TB"],
                "routeAddresses": ["0xTB", "0xTZ", "0xTA", "0xTB"],
                "pairSymbols": ["TB -> TZ", "TZ:TA", "TA -> TB"],
                "poolIds": ["pool-tb-tz", "pool-tz-ta", "pool-ta-tb"],
                "routePools": [
                    _pool("pool-tb-tz", ["TB", "TZ"], "yc"),
                    _pool("pool-tz-ta", ["TZ", "TA"], "zc"),
                    _pool("pool-ta-tb", ["TA", "TB"], "zc"),
                ],
            },
        ],
    }


def test_group_closed_loop_routes_by_flash_loan_token_groups_start_end():
    groups = group_closed_loop_routes_by_flash_loan_token(_snapshot())

    assert [group["flashLoanToken"]["symbol"] for group in groups] == ["TA", "TB"]
    ta = groups[0]
    assert ta["startToken"]["symbol"] == "TA"
    assert ta["endToken"]["symbol"] == "TA"
    assert ta["flashLoanToken"]["address"] == "0xTA"
    assert ta["borrowable"] is True
    assert ta["routeCount"] == 1
    assert ta["poolDependencyCount"] == 6


def test_group_key_uses_address_not_just_symbol_for_duplicate_symbols():
    snapshot = _snapshot()
    snapshot["closedLoopRouteSummaries"].append(
        {
            "routeKey": "route-ta-alt",
            "anchorSymbol": "TA",
            "counterSymbol": "TZ",
            "bridgeSymbol": "TB",
            "pathText": "TA -> TB -> TZ -> TA",
            "pathSymbols": ["TA", "TB", "TZ", "TA"],
            "routeAddresses": ["0xTA_ALT", "0xTB", "0xTZ", "0xTA_ALT"],
            "pairSymbols": ["TA -> TB", "TB:TZ", "TZ -> TA"],
            "poolIds": ["pool-ta-alt-tb", "pool-tb-tz", "pool-tz-ta-alt"],
            "routePools": [
                _pool("pool-ta-alt-tb", ["TA", "TB"], "yc"),
                _pool("pool-tb-tz", ["TB", "TZ"], "yc"),
                _pool("pool-tz-ta-alt", ["TZ", "TA"], "zc"),
            ],
        }
    )

    groups = group_closed_loop_routes_by_flash_loan_token(snapshot)
    ta_groups = [group for group in groups if group["flashLoanToken"]["symbol"] == "TA"]

    assert len(ta_groups) == 2
    assert {group["flashLoanToken"]["address"] for group in ta_groups} == {"0xTA", "0xTA_ALT"}


def test_group_closed_loop_routes_dedupes_pool_dependencies():
    groups = group_closed_loop_routes_by_flash_loan_token(_snapshot())
    ta = next(group for group in groups if group["flashLoanToken"]["symbol"] == "TA")
    pool_ids = {item["pool"] for item in ta["poolDependencies"]}

    assert "pool-ta-tz" in pool_ids
    assert "pool-tz-tb" in pool_ids
    assert "pool-tb-ta" in pool_ids
    assert "pool-tz-ta" in pool_ids


def test_closed_loop_route_groups_payload_summary():
    payload = closed_loop_route_groups_payload(_snapshot())

    assert payload["ok"] is True
    assert payload["chainId"] == 46630
    assert payload["summary"]["groupCount"] == 2
    assert payload["summary"]["closedLoopRouteCount"] == 2
    assert payload["summary"]["borrowableSymbolCount"] == 2


def test_closed_loop_route_groups_payload_can_omit_routes():
    payload = closed_loop_route_groups_payload(
        _snapshot(),
        include_routes=False,
    )

    assert payload["includeRoutes"] is False
    assert payload["summary"]["groupCount"] == 2
    for group in payload["groups"]:
        assert "routes" not in group
        assert group["routeCount"] >= 1
        assert group["poolDependencyCount"] >= 3


def test_group_overview_paths_by_flash_loan_token():
    overview_paths = [
        {
            "routeSymbols": ["TA", "TZ", "TB", "TA"],
            "overviewRoute": {"anchorBorrowSymbol": "TA"},
            "flashLoanLinkage": {"borrow_symbols": ["TA"]},
            "executionPools": [_pool("pool-ta-tz", ["TA", "TZ"], "yc")],
        },
        {
            "routeSymbols": ["TB", "TZ", "TA", "TB"],
            "overviewRoute": {"anchorBorrowSymbol": "TB"},
            "flashLoanLinkage": {"borrow_symbols": ["TB"]},
            "executionPools": [_pool("pool-tb-tz", ["TB", "TZ"], "yc")],
        },
    ]

    groups = group_overview_paths_by_flash_loan_token(overview_paths, borrowable_symbols=["TA", "TB"])

    assert [group["flashLoanToken"]["symbol"] for group in groups] == ["TA", "TB"]
    assert groups[0]["pathCount"] == 1
    assert groups[0]["poolDependencyCount"] == 1


def test_group_overview_paths_dedupes_reverse_closed_loop():
    overview_paths = [
        {
            "routeSymbols": ["TA", "TZ", "TB", "TA"],
            "overviewRoute": {"anchorBorrowSymbol": "TA"},
            "flashLoanLinkage": {"borrow_symbols": ["TA"]},
            "executionPools": [_pool("pool-ta-tz", ["TA", "TZ"], "yc")],
        },
        {
            "routeSymbols": ["TA", "TB", "TZ", "TA"],
            "overviewRoute": {"anchorBorrowSymbol": "TA"},
            "flashLoanLinkage": {"borrow_symbols": ["TA"]},
            "executionPools": [_pool("pool-ta-tb", ["TA", "TB"], "yc")],
        },
    ]

    groups = group_overview_paths_by_flash_loan_token(overview_paths, borrowable_symbols=["TA"])

    assert len(groups) == 1
    assert groups[0]["pathCount"] == 1
    assert groups[0]["poolDependencyCount"] == 2
