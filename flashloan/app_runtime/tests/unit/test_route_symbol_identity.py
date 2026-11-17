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


# --- REG-TESTNET-TOKEN-IDENTITY -------------------------------------------
#
# The testnet allowlist used to carry a stale placeholder alias for the TSLA
# address ("0XC9F9C8", category "meme") next to the authoritative stock-token
# identity ("TSLA", category "robinhood_stock"). Both entries pointed at the
# same address, so the reverse symbol lookup dropped that address as
# ambiguous and every route through TSLA came back as
# "UNRESOLVED:0xC9F9...". The closed-loop worker then registered its group
# under the unresolved key, and the manual review could never find it.
#
# The invariant is therefore: one address carries exactly one symbol.


def test_testnet_stock_tokens_resolve_to_their_authoritative_symbol():
    stock_addresses = {
        "AMD": "0x71178BAc73cBeb415514eB542a8995b82669778d",
        "AMZN": "0x5884aD2f920c162CFBbACc88C9C51AA75eC09E02",
        "NFLX": "0x3b8262A63d25f0477c4DDE23F83cfe22Cb768C93",
        "PLTR": "0x1FBE1a0e43594b3455993B5dE5Fd0A7A266298d0",
        "TSLA": "0xC9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E",
    }

    for symbol, address in stock_addresses.items():
        assert route_symbol_identity.trusted_symbol_from_address(
            address,
            chain_id=46630,
        ) == symbol


def test_testnet_allowlist_never_maps_one_address_to_two_symbols():
    """A duplicated address makes the reverse lookup ambiguous."""
    path = route_symbol_identity._chain_token_allowlist_path(46630)
    assert path is not None and path.is_file()

    mapping, ambiguous = route_symbol_identity._load_json_tokens(path)
    assert not ambiguous

    by_address: dict[str, list[str]] = {}
    for symbol, address in mapping.items():
        by_address.setdefault(str(address).strip().lower(), []).append(symbol)
    duplicated = {
        address: symbols
        for address, symbols in by_address.items()
        if len(symbols) > 1
    }
    assert not duplicated, f"ambiguous testnet token addresses: {duplicated}"


def test_testnet_official_route_addresses_cover_the_stock_triangle():
    """The three-hop stock route must resolve end to end."""
    symbols = ["ETH", "AMZN", "AMD", "ETH"]

    addresses = route_symbol_identity.official_route_addresses(symbols, chain_id=46630)

    assert addresses == [
        route_symbol_identity.official_route_addresses(["ETH"], chain_id=46630)[0],
        "0x5884aD2f920c162CFBbACc88C9C51AA75eC09E02",
        "0x71178BAc73cBeb415514eB542a8995b82669778d",
        route_symbol_identity.official_route_addresses(["ETH"], chain_id=46630)[0],
    ]
