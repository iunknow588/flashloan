from types import SimpleNamespace

from execution.receipt_formatter import format_tx_receipt
from execution.static_call import liquidation_request_tuple, simulate_request_liquidation_static_call
from web.liquidation_account_backfill import AccountBackfillService
from web.liquidation_scan_presenter import account_tier_summary, display_health_rows


def test_account_backfill_service_tracks_progress_and_stop():
    service = AccountBackfillService()

    service.set_progress({"from_block": 10, "to_block": 19, "current_to_block": 14})
    stopped = service.request_stop()

    assert service.cache["progress"]["percent"] == 50.0
    assert service.cache["progress"]["scanned_blocks"] == 5
    assert stopped["stop_requested"] is True
    assert stopped["stage"] == "idle"


def test_presenter_sorts_display_rows_and_summarizes_tiers():
    rows = [
        {"account": "b", "status": "healthy", "health_factor": 1.5},
        {"account": "a", "status": "liquidatable", "health_factor": 0.9},
    ]

    display = display_health_rows(rows, limit=1, band=lambda value: "red" if value < 1 else "green")

    assert display[0]["account"] == "a"
    assert display[0]["health_factor_band"] == "red"
    assert account_tier_summary({"active_count": 4, "hot_count": 1, "warm_count": 2, "cold_count": 1})[
        "classified_count"
    ] == 4


def test_static_call_tuple_and_receipt_formatter_are_stable():
    request = {
        "user": "0x0000000000000000000000000000000000000001",
        "collateralAsset": "0x0000000000000000000000000000000000000002",
        "debtAsset": "0x0000000000000000000000000000000000000003",
        "debtToCover": "100",
        "minCollateralSwapOut": "90",
        "minProfitAmount": "5",
        "deadline": "123",
        "gasLimit": "456",
        "swapPath": ["0x0000000000000000000000000000000000000003"],
    }
    receipt = SimpleNamespace(
        transactionHash=b"\x12\x34",
        blockNumber=7,
        gasUsed=21000,
        effectiveGasPrice=25,
        status=1,
    )

    assert liquidation_request_tuple(request)[3:8] == (100, 90, 5, 123, 456)
    assert format_tx_receipt(receipt) == {
        "transaction_hash": "1234",
        "block_number": 7,
        "gas_used": 21000,
        "effective_gas_price": 25,
        "status": 1,
    }


def test_static_call_error_is_redacted(monkeypatch):
    rpc_url = "https://rpc.example/path?token=abc123"
    private_key = "0x" + "a" * 64
    monkeypatch.setenv("AVALANCHE_RPC_URL", rpc_url)

    class FakeCall:
        @staticmethod
        def call(params):
            raise RuntimeError(f"static failed: {rpc_url} private_key={private_key}")

    class FakeFunctions:
        @staticmethod
        def requestLiquidation(request):
            return FakeCall()

    class FakeContract:
        functions = FakeFunctions()

    class FakeEth:
        @staticmethod
        def contract(address, abi):
            return FakeContract()

    class FakeWeb3:
        eth = FakeEth()

    result = simulate_request_liquidation_static_call(
        FakeWeb3(),
        executor_address="0x0000000000000000000000000000000000000001",
        owner_address="0x0000000000000000000000000000000000000002",
        request={
            "user": "0x0000000000000000000000000000000000000003",
            "collateralAsset": "0x0000000000000000000000000000000000000004",
            "debtAsset": "0x0000000000000000000000000000000000000005",
            "debtToCover": 100,
            "minCollateralSwapOut": 90,
            "minProfitAmount": 5,
            "deadline": 123,
            "gasLimit": 456,
            "swapPath": ["0x0000000000000000000000000000000000000005"],
        },
    )

    assert result["status"] == "error"
    assert rpc_url not in result["error"]
    assert private_key not in result["error"]
    assert "abc123" not in result["error"]
    assert "[REDACTED]" in result["error"]
