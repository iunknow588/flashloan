from dex.runtime.cow_candidate_queue import candidate_signature


def test_candidate_signature_uses_route_addresses_when_available():
    route_a = {
        "network": "robinhood-testnet",
        "pair": "WETH -> ETH -> 0XC9F9C8 -> WETH",
        "route_path": ["WETH", "ETH", "0XC9F9C8", "WETH"],
        "routeAddresses": [
            "0x7943e237c7F95DA44E0301572D358911207852Fa",
            "0x0000000000000000000000000000000000000000",
            "0xc9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E",
            "0x7943e237c7F95DA44E0301572D358911207852Fa",
        ],
    }
    route_b = {
        "network": "robinhood-testnet",
        "pair": "WETH -> ETH -> 0XC9F9C8 -> WETH",
        "route_path": ["WETH", "ETH", "0XC9F9C8", "WETH"],
        "routeAddresses": [
            "0x33e4191705c386532ba27cBF171Db86919200B94",
            "0x0000000000000000000000000000000000000000",
            "0xc9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E",
            "0x33e4191705c386532ba27cBF171Db86919200B94",
        ],
    }

    assert candidate_signature(route_a) != candidate_signature(route_b)


def test_candidate_signature_falls_back_to_symbol_path_when_no_addresses():
    attempt = {
        "network": "robinhood-testnet",
        "pair": "TZ -> TB -> TA -> TZ",
        "route_path": ["TZ", "TB", "TA", "TZ"],
    }

    assert "TZ" in candidate_signature(attempt)
