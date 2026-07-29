from execution.dex_costs import USDC
from execution.plan_quotes import quote_execution_plan, quote_token


class FakeCall:
    def __init__(self, value):
        self.value = value

    def call(self):
        return self.value


class FakeFunctions:
    def __init__(self, rates, decimals):
        self.rates = rates
        self.decimals = decimals

    def getAmountsOut(self, amount_in, path):
        amounts = [int(amount_in)]
        current = int(amount_in)
        for token_in, token_out in zip(path, path[1:]):
            rate = self.rates[(token_in.lower(), token_out.lower())]
            human_in = current / 10 ** self.decimals[token_in.lower()]
            human_out = human_in * rate
            current = int(round(human_out * 10 ** self.decimals[token_out.lower()]))
            amounts.append(current)
        return FakeCall(amounts)

    def getAmountsIn(self, amount_out, path):
        amounts = [0] * len(path)
        amounts[-1] = int(amount_out)
        current = int(amount_out)
        for index in range(len(path) - 1, 0, -1):
            token_in = path[index - 1]
            token_out = path[index]
            rate = self.rates[(token_in.lower(), token_out.lower())]
            human_out = current / 10 ** self.decimals[token_out.lower()]
            human_in = human_out / rate
            current = int(round(human_in * 10 ** self.decimals[token_in.lower()]))
            amounts[index - 1] = current
        return FakeCall(amounts)


class FakeRouter:
    def __init__(self, rates, decimals):
        self.functions = FakeFunctions(rates, decimals)


class FakeEth:
    def __init__(self, router):
        self.router = router

    def contract(self, address, abi):
        return self.router


class FakeWeb3:
    next_router = None

    @staticmethod
    def HTTPProvider(rpc_url, request_kwargs=None):
        return rpc_url

    @staticmethod
    def to_checksum_address(address):
        return address

    def __init__(self, provider):
        self.eth = FakeEth(self.next_router)


def test_quote_execution_plan_values_non_usdc_profit_asset(monkeypatch):
    avax = quote_token("AVAXUSDT")
    aave = quote_token("AAVEUSDT")
    decimals = {
        avax.token_address.lower(): avax.decimals,
        USDC.lower(): 6,
        aave.token_address.lower(): aave.decimals,
    }
    rates = {
        (avax.token_address.lower(), USDC.lower()): 20.0,
        (USDC.lower(), avax.token_address.lower()): 0.05,
        (USDC.lower(), aave.token_address.lower()): 0.5,
        (aave.token_address.lower(), USDC.lower()): 3.0,
    }
    FakeWeb3.next_router = FakeRouter(rates, decimals)
    monkeypatch.setattr("execution.plan_quotes.Web3", FakeWeb3)

    plan = {
        "buy_steps": [
            {
                "rank": 1,
                "from_symbol": "AVAXUSDT",
                "to_symbol": "USDC",
                "input_amount": 1.0,
                "output_amount": 20.0,
            }
        ],
        "sell_steps": [
            {
                "rank": 1,
                "from_symbol": "USDC",
                "to_symbol": "AAVEUSDT",
                "input_amount": 20.0,
                "output_amount": 10.0,
            }
        ],
        "repay_steps": [
            {
                "rank": 1,
                "from_symbol": "AAVEUSDT",
                "to_symbol": "AVAXUSDT",
                "input_amount": 10.0,
                "output_amount": 1.0,
            }
        ],
    }

    quote = quote_execution_plan(plan, "fake-rpc", router_address="0x0000000000000000000000000000000000000001")

    assert quote["errors"] == []
    assert quote["viable"] is True
    assert quote["profit_legs"][0]["profit_symbol"] == "AAVEUSDT"
    assert quote["profit_legs"][0]["profit_input_amount"] > 3.3
    assert quote["quoted_profit_usdc"] > 9.9
