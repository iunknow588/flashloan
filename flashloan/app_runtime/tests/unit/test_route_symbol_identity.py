from core import route_symbol_identity


TESTNET_ADDRESSES = {
    "TZ": "0x05a31b34a1bf8EDB08Cbfa2E9d860E6d0939313E",
    "TB": "0x28cc9a0b94aD34A3A0CB2734c8d58aD95862C6c4",
    "TA": "0xB169FE96425783D7BA7a9F905fe8dE5f80E3Fdc8",
    "ETH": "0x0000000000000000000000000000000000000000",
    "WETH": "0x7943e237c7F95DA44E0301572D358911207852Fa",
}


def test_official_route_addresses_uses_chain_owned_allowlist():
    symbols = ["TZ", "TB", "TA", "TZ"]

    assert route_symbol_identity.official_route_addresses(symbols, chain_id=46630) == [
        TESTNET_ADDRESSES[symbol] for symbol in symbols
    ]


def test_route_symbols_resolve_unresolved_placeholder_from_trusted_address():
    symbols = ["ETH", "0XABCDEF12", "WETH"]
    addresses = [
        TESTNET_ADDRESSES["ETH"],
        TESTNET_ADDRESSES["TA"],
        TESTNET_ADDRESSES["WETH"],
    ]

    assert route_symbol_identity.normalize_route_symbols_from_addresses(
        route_symbols=symbols,
        route_addresses=addresses,
        chain_id=46630,
    ) == ["ETH", "TA", "WETH"]
    assert route_symbol_identity.trusted_symbol_from_address(
        TESTNET_ADDRESSES["TA"],
        chain_id=46630,
    ) == "TA"


def test_official_route_addresses_rejects_partial_or_unknown_routes():
    assert route_symbol_identity.official_route_addresses(
        ["WETH", "UNKNOWN_SYMBOL"],
        chain_id=46630,
    ) == []
    assert route_symbol_identity.official_route_addresses(
        ["WETH", "ETH"],
        chain_id=None,
    ) == []
