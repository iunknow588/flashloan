from pathlib import Path

from bot.execution import venus_markets


VTOKEN = "0x0000000000000000000000000000000000000011"
UNDERLYING = "0x0000000000000000000000000000000000000022"
COMPTROLLER = "0x0000000000000000000000000000000000000033"
ORACLE = "0x0000000000000000000000000000000000000044"


class FakeCall:
    def __init__(self, value):
        self.value = value

    def call(self):
        return self.value


class FakeFunctions:
    def __init__(self, values):
        self.values = values

    def __getattr__(self, name):
        def invoke(*args):
            value = self.values[name]
            if callable(value):
                value = value(*args)
            return FakeCall(value)

        return invoke


class FakeContract:
    def __init__(self, values):
        self.functions = FakeFunctions(values)


class FakeEth:
    block_number = 123


class FakeW3:
    eth = FakeEth()


def test_build_venus_market_static_table_reads_and_writes(monkeypatch, tmp_path):
    contracts = {
        COMPTROLLER.lower(): FakeContract(
            {
                "getAllMarkets": [VTOKEN],
                "closeFactorMantissa": 500_000_000_000_000_000,
                "liquidationIncentiveMantissa": 1_100_000_000_000_000_000,
                "oracle": ORACLE,
                "markets": lambda _address: (True, 800_000_000_000_000_000, False),
            }
        ),
        VTOKEN.lower(): FakeContract(
            {
                "symbol": "vUSDT",
                "name": "Venus USDT",
                "decimals": 8,
                "underlying": UNDERLYING,
                "exchangeRateStored": 2,
                "borrowIndex": 3,
                "totalSupply": 4,
                "totalBorrows": 5,
                "getCash": 6,
            }
        ),
        UNDERLYING.lower(): FakeContract({"symbol": "USDT", "decimals": 18}),
        ORACLE.lower(): FakeContract({"getUnderlyingPrice": 1_000_000_000_000_000_000}),
    }

    monkeypatch.setattr(venus_markets, "_contract", lambda _w3, address, _abi: contracts[address.lower()])

    table = venus_markets.build_venus_market_static_table(FakeW3(), comptroller_address=COMPTROLLER, rpc_url="http://rpc")
    paths = venus_markets.write_venus_market_static_table(table, tmp_path)

    assert table["market_count"] == 1
    assert table["liquidation_incentive_source"] == "comptroller"
    assert table["markets"][0]["underlying_symbol"] == "USDT"
    assert table["markets"][0]["stable"] is True
    assert Path(paths["json"]).exists()
    assert "vUSDT" in Path(paths["markdown"]).read_text(encoding="utf-8")
