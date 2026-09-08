from market.tools.build_bnb_signal_route_report import build_report


def _hop(token_in, token_out, amount_in, amount_out, pool):
    return {
        "tokenIn": token_in,
        "tokenOut": token_out,
        "pool": pool,
        "protocol": "v3",
        "fee": 500,
        "quality": "recommended",
        "tvlUsd": 1000000,
        "quoterGasEstimate": 90000,
        "amountIn": str(amount_in),
        "amountOut": str(amount_out),
    }


def _observation(path, final_amount):
    symbols = path.split(" -> ")
    first_amount_out = 1.6 if symbols[1] == "WBNB" else 0.01
    second_amount_out = 0.01 if symbols[2] == "BTCB" else 1.6
    return {
        "quoteOk": True,
        "signal": {
            "pair": "BNBUSDT / BTCUSDT",
            "xBinanceSymbol": "BNBUSDT",
            "yBinanceSymbol": "BTCUSDT",
            "xBaseSymbol": "WBNB",
            "yBaseSymbol": "BTCB",
            "xChangePercent": 0.1,
            "yChangePercent": -0.1,
            "windowSpreadPercent": 0.2,
        },
        "quote": {
            "path": path,
            "routeSymbols": symbols,
            "blockNumber": 123,
            "initialAmount": "1000",
            "finalAmount": str(final_amount),
            "grossProfit": str(final_amount - 1000),
            "hops": [
                _hop(symbols[0], symbols[1], 1000, first_amount_out, "0x0000000000000000000000000000000000000001"),
                _hop(symbols[1], symbols[2], first_amount_out, second_amount_out, "0x0000000000000000000000000000000000000002"),
                _hop(symbols[2], symbols[3], second_amount_out, final_amount, "0x0000000000000000000000000000000000000003"),
            ],
        },
    }


def test_report_groups_one_ui_trigger_into_three_tables_and_uses_best_route():
    records = [
        {
            "recordedAt": "2026-08-15T08:00:00Z",
            "marketState": {
                "top": [{"base_symbol": "BNB", "current_price": 600}],
                "bottom": [{"base_symbol": "BTC", "current_price": 60000}],
            },
            "result": {
                "observations": [
                    _observation("USDT -> WBNB -> BTCB -> USDT", 999),
                    _observation("USDT -> BTCB -> WBNB -> USDT", 998),
                ]
            },
        }
    ]

    report = build_report(records)

    assert report.count("## 信号 ") == 1
    assert report.count("<table>") == 3
    assert "本次候选闭环 `2` 条" in report
    assert "- 详细路径：`USDT -> BTCB -> WBNB -> USDT`" in report
    assert "- 反向对照：`USDT -> WBNB -> BTCB -> USDT`；最终回款 `999`。" in report
    assert "998 &lt; 1,000" in report
