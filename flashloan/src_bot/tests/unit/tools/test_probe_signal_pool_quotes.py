from tools import probe_signal_pool_quotes as quotes


USDC = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
WBTC_E = "0x50b7545627a5162F82A992c33b87aDc75187B218"
BTC_B = "0x152b9d0FdC40C096757F570A51E494bd4b943E50"


def _btc_cache_with_wbtc_first() -> dict:
    return {
        "pools": [
            {
                "tokenX_symbol": "WBTC.e",
                "tokenY_symbol": "USDC",
                "tokenX": WBTC_E,
                "tokenY": USDC,
                "pools": [{"adapterKind": 1, "pool": "0x0000000000000000000000000000000000000001"}],
            },
            {
                "tokenX_symbol": "BTC.b",
                "tokenY_symbol": "USDC",
                "tokenX": BTC_B,
                "tokenY": USDC,
                "pools": [{"adapterKind": 1, "pool": "0x0000000000000000000000000000000000000002"}],
            },
        ]
    }


def test_btc_b_symbol_does_not_match_wbtc_e_pool_first() -> None:
    entry = quotes._oriented_entry(_btc_cache_with_wbtc_first(), "USDC", "BTC.B")

    assert entry is not None
    assert entry["tokenX"] == USDC
    assert entry["tokenY"] == BTC_B
    assert entry["pools"][0]["pool"] == "0x0000000000000000000000000000000000000002"


def test_explicit_wbtc_e_still_matches_wbtc_e_pool() -> None:
    entry = quotes._oriented_entry(_btc_cache_with_wbtc_first(), "USDC", "WBTC.e")

    assert entry is not None
    assert entry["tokenX"] == USDC
    assert entry["tokenY"] == WBTC_E
    assert entry["pools"][0]["pool"] == "0x0000000000000000000000000000000000000001"


def test_chained_route_uses_previous_recommended_output_as_next_input(monkeypatch) -> None:
    calls = []
    outputs = {
        "ux_usdc_to_x": [120, 150],
        "xy_x_to_y": [7],
        "uy_usdc_to_y": [1_100_000_000],
    }

    def fake_quote_trade_direction(w3, quoter, trade, **kwargs):
        calls.append((trade["label"], kwargs["amount_in"]))
        return [
            {
                "slot": {"pool": f"0x{index + 1:040x}"},
                "amountOut": amount_out,
            }
            for index, amount_out in enumerate(outputs[trade["label"]])
        ]

    monkeypatch.setattr(quotes, "_quote_trade_direction", fake_quote_trade_direction)
    trades = [
        {"label": "ux_usdc_to_x"},
        {"label": "uy_usdc_to_y"},
        {"label": "xy_x_to_y"},
    ]

    result = quotes._chained_route_quotes(
        object(),
        "0x0000000000000000000000000000000000000001",
        trades,
        route_path=["USDC", "WAVAX", "BTC.B", "USDC"],
        usdc_amount=1_000_000_000,
        block_identifier=123,
    )

    assert result["status"] == "quoted"
    assert calls == [
        ("ux_usdc_to_x", 1_000_000_000),
        ("xy_x_to_y", 150),
        ("uy_usdc_to_y", 7),
    ]
    assert result["uX"]["recommendedAmountOut"] == "150"
    assert result["xY"]["amountIn"] == "150"
    assert result["xY"]["recommendedAmountOut"] == "7"
    assert result["yC"]["amountIn"] == "7"
    assert result["yC"]["recommendedAmountOut"] == "1100000000"
