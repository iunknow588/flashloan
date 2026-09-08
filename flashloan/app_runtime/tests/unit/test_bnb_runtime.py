import pytest

from bot.execution import bnb_runtime


class FakeEth:
    gas_price = 3_000_000_000
    block_number = 123

    def contract(self, address, abi):
        class Router:
            class functions:
                @staticmethod
                def getAmountsOut(amount_in, path):
                    class Call:
                        @staticmethod
                        def call():
                            return [amount_in, 600 * 10**18]

                    return Call()

        return Router()


class FakeW3:
    eth = FakeEth()


def test_connect_bnb_rpc_with_retries_backs_off_on_429():
    calls = []
    sleeps = []

    def connector(_urls):
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("429 too many requests")
        return FakeW3(), "https://bsc.example"

    w3, url, metadata = bnb_runtime.connect_bnb_rpc_with_retries(
        ["https://bsc.example"],
        attempts=3,
        backoff_seconds=0.5,
        connector=connector,
        sleeper=sleeps.append,
    )

    assert w3 is not None
    assert url == "https://bsc.example"
    assert metadata["attempts"] == 3
    assert metadata["rate_limited"] is True
    assert sleeps == [0.5, 1.0]


def test_connect_bnb_rpc_with_retries_raises_after_failures():
    with pytest.raises(RuntimeError, match="no usable BNB RPC"):
        bnb_runtime.connect_bnb_rpc_with_retries(
            ["https://bsc.example"],
            attempts=1,
            connector=lambda _urls: (_ for _ in ()).throw(RuntimeError("down")),
            sleeper=lambda _seconds: None,
        )


def test_read_bnb_runtime_metrics_reports_gas_and_native_price():
    metrics = bnb_runtime.read_bnb_runtime_metrics(FakeW3())

    assert metrics["gas_price"]["gas_price_gwei"] == 3.0
    assert metrics["native_usd_price"]["ok"] is True
    assert metrics["native_usd_price"]["bnb_usd"] == 600
