from execution.liquidation_realtime_params import read_aave_flashloan_premium


def test_read_aave_flashloan_premium_reads_pool_value():
    class FakePremiumCall:
        @staticmethod
        def call():
            return 5

    class FakeFunctions:
        @staticmethod
        def FLASHLOAN_PREMIUM_TOTAL():
            return FakePremiumCall()

    class FakeContract:
        functions = FakeFunctions()

    class FakeEth:
        block_number = 123

        @staticmethod
        def contract(address, abi):
            return FakeContract()

    class FakeWeb3:
        def __init__(self, provider):
            self.eth = FakeEth()

        @staticmethod
        def HTTPProvider(*args, **kwargs):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return value

    result = read_aave_flashloan_premium(
        "https://rpc.example",
        "0x0000000000000000000000000000000000000001",
        fallback_percent=0.05,
        web3_class=FakeWeb3,
    )

    assert result["premium_bps"] == 5
    assert result["premium_percent"] == 0.05
    assert result["source"] == "aave_pool"
    assert result["block_number"] == 123


def test_read_aave_flashloan_premium_falls_back_with_source():
    class FakeWeb3:
        @staticmethod
        def HTTPProvider(*args, **kwargs):
            raise RuntimeError("rpc down")

    result = read_aave_flashloan_premium(
        "https://rpc.example",
        "0x0000000000000000000000000000000000000001",
        fallback_percent=0.09,
        web3_class=FakeWeb3,
    )

    assert result["premium_bps"] == 9
    assert result["premium_percent"] == 0.09
    assert result["source"] == "fallback_config"
    assert "rpc down" in result["error"]
