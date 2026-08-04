from execution.dynamic_quote import DynamicQuoteConfig, quote_dynamic_candidate, route_symbols


class FakeCall:
    def __init__(self, value):
        self.value = value

    def call(self):
        return self.value


class FakeFunctions:
    def getAmountsOut(self, amount_in, path):
        return FakeCall([amount_in, amount_in * 2])

    def getAmountsIn(self, amount_out, path):
        return FakeCall([max(1, amount_out // 4), amount_out])


class FakeRouter:
    functions = FakeFunctions()


def test_route_symbols_match_dynamic_contract_shape():
    assert route_symbols(0, "AVAXUSDT", "AAVEUSDT") == ["AVAXUSDT", "USDC", "AAVEUSDT", "AVAXUSDT"]
    assert route_symbols(2, "AVAXUSDT", "AAVEUSDT") == ["AVAXUSDT", "AAVEUSDT", "USDC", "AVAXUSDT"]


def test_quote_dynamic_candidate_selects_viable_quote_without_marking_executable():
    result = quote_dynamic_candidate(
        FakeRouter(),
        {"x_symbol": "AVAXUSDT", "y_symbol": "AAVEUSDT"},
        DynamicQuoteConfig(amount_x_units=1000, amount_y_units=1000, premium_bps=5),
    )

    assert result["dex_quote_verified"] is True
    assert result["net_profit_verified"] is False
    assert result["executable_signal"] is False
    assert result["best_quote"]["viable"] is True
