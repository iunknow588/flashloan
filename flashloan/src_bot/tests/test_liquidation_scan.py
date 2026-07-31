from pathlib import Path

import pytest

from execution.liquidation_scan import (
    LiquidationScanConfig,
    build_liquidation_execution_plan,
    build_liquidation_candidates,
    discover_borrower_addresses,
    health_factor_band,
    classify_health_factor,
    estimate_liquidation_profit,
    load_account_addresses,
    scan_account_health,
    watched_health_rows,
    split_candidate_accounts,
)
from execution.liquidation_payload import build_liquidation_execution_payload


def test_load_account_addresses_deduplicates_and_skips_invalid(tmp_path: Path):
    path = tmp_path / "accounts.txt"
    path.write_text(
        "\n".join(
            [
                "0x0000000000000000000000000000000000000001",
                "bad",
                "0x0000000000000000000000000000000000000001",
                "0x0000000000000000000000000000000000000002",
            ]
        ),
        encoding="utf-8",
    )

    assert load_account_addresses(path) == [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
    ]


def test_classify_health_factor():
    assert classify_health_factor(0.99, 1.05, 1.0) == "liquidatable"
    assert classify_health_factor(1.02, 1.05, 1.0) == "warning"
    assert classify_health_factor(1.20, 1.05, 1.0) == "healthy"


def test_health_factor_band_thresholds():
    assert health_factor_band(1.30) == "green"
    assert health_factor_band(1.20) == "beige"
    assert health_factor_band(1.10) == "yellow"
    assert health_factor_band(1.00) == "orange"
    assert health_factor_band(0.99) == "red"


def test_estimate_liquidation_profit_subtracts_flashloan_slippage_and_gas():
    result = estimate_liquidation_profit(
        total_debt_base=1000,
        liquidation_bonus_percent=5,
        flashloan_fee_percent=0.05,
        dex_slippage_percent=0.10,
        gas_cost_usd=1,
        repay_fraction=0.5,
    )

    assert result["repay_base"] == 500
    assert result["gross_profit_base"] == pytest.approx(25)
    assert result["fee_base"] == pytest.approx(0.75)
    assert result["net_profit_base"] == pytest.approx(23.25)
    assert result["profitable"]


def test_build_liquidation_candidates_uses_amount_to_pass_for_profit(monkeypatch):
    from execution import liquidation_scan

    class FakeProvider:
        class functions:
            @staticmethod
            def getLiquidationInfo(user, collateral, debt):
                class Call:
                    @staticmethod
                    def call():
                        return (
                            (0, 2000, 0, 0, 0, 0),
                            (100, 200, collateral, 500, 500),
                            (100, 400, debt, 300, 300),
                            120,
                            80,
                            0,
                            40,
                        )

                return Call()

    class FakeContract:
        functions = FakeProvider.functions

    class FakeEth:
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

    monkeypatch.setattr(liquidation_scan, "Web3", FakeWeb3)

    positions = [
        {"token_address": "0x1", "symbol": "COLL", "usage_as_collateral_enabled": True, "current_a_token_balance": 500, "current_stable_debt": 0, "current_variable_debt": 0},
        {"token_address": "0x2", "symbol": "DEBT", "usage_as_collateral_enabled": False, "current_a_token_balance": 0, "current_stable_debt": 300, "current_variable_debt": 0},
    ]
    candidates = build_liquidation_candidates(
        "https://rpc.example",
        "0xabc",
        positions,
        "0xdef",
        LiquidationScanConfig(close_factor=0.5),
    )

    assert candidates[0]["estimated_profit"]["repay_base_source"] == "amount_to_pass_to_liquidation_call"
    assert candidates[0]["estimated_profit"]["repay_base"] == pytest.approx(40)


def test_build_liquidation_candidates_uses_realtime_flashloan_premium(monkeypatch):
    from execution import liquidation_scan

    class FakeProvider:
        class functions:
            @staticmethod
            def getLiquidationInfo(user, collateral, debt):
                class Call:
                    @staticmethod
                    def call():
                        return (
                            (0, 2000, 0, 0, 0, 0),
                            (100, 200, collateral, 500, 500),
                            (100, 400, debt, 300, 300),
                            120,
                            80,
                            0,
                            40,
                        )

                return Call()

    class FakeContract:
        functions = FakeProvider.functions

    class FakeEth:
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

    monkeypatch.setattr(liquidation_scan, "Web3", FakeWeb3)
    positions = [
        {"token_address": "0x1", "symbol": "COLL", "usage_as_collateral_enabled": True, "current_a_token_balance": 500, "current_stable_debt": 0, "current_variable_debt": 0},
        {"token_address": "0x2", "symbol": "DEBT", "usage_as_collateral_enabled": False, "current_a_token_balance": 0, "current_stable_debt": 300, "current_variable_debt": 0},
    ]

    candidates = build_liquidation_candidates(
        "https://rpc.example",
        "0xabc",
        positions,
        "0xdef",
        LiquidationScanConfig(close_factor=0.5, flashloan_fee_percent=1.0),
        realtime_params={"flashloan_premium": {"premium_percent": 0.05, "premium_bps": 5, "source": "aave_pool", "block_number": 456}},
    )

    profit = candidates[0]["estimated_profit"]
    assert profit["flashloan_premium_source"] == "aave_pool"
    assert profit["flashloan_premium_bps"] == 5
    assert profit["flashloan_premium_block_number"] == 456
    assert candidates[0]["parameter_sources"]["flashloan_premium_source"] == "aave_pool"


def test_split_candidate_accounts():
    groups = split_candidate_accounts(
        [
            {"account": "a", "health_factor": 0.99},
            {"account": "b", "health_factor": 1.02},
            {"account": "c", "health_factor": 1.20},
        ],
        warning_threshold=1.05,
        liquidation_threshold=1.0,
    )

    assert [item["account"] for item in groups["liquidation_accounts"]] == ["a"]
    assert [item["account"] for item in groups["warning_accounts"]] == ["b"]
    assert [item["account"] for item in groups["healthy_accounts"]] == ["c"]


def test_watched_health_rows_filters_above_threshold():
    rows = watched_health_rows(
        [
            {"account": "a", "health_factor": 1.49},
            {"account": "b", "health_factor": 1.31},
            {"account": "c", "health_factor": 1.29},
            {"account": "d", "health_factor": 0.99},
            {"account": "e", "health_factor": 1.50},
        ]
    )

    assert [item["account"] for item in rows] == ["d", "c", "b", "a"]
    assert [item["health_factor_band"] for item in rows] == ["red", "beige", "green", "green"]


def test_scan_account_health_uses_fetcher(monkeypatch):
    from execution import liquidation_scan

    def fake_fetch(pool_address, account, rpc_url):
        return {
            "account": account,
            "total_collateral_base": 1200,
            "total_debt_base": 1000,
            "available_borrows_base": 0,
            "current_liquidation_threshold": 8000,
            "ltv": 7500,
            "health_factor": 0.98,
        }

    monkeypatch.setattr(liquidation_scan, "fetch_user_account_data", fake_fetch)

    rows = scan_account_health(
        ["0x0000000000000000000000000000000000000001"],
        "0x0000000000000000000000000000000000000002",
        "https://rpc.example",
        LiquidationScanConfig(max_candidates=10),
    )

    assert rows[0]["status"] == "liquidatable"
    assert rows[0]["liquidation_profit"]["profitable"]


def test_discover_borrower_addresses_scans_full_window_after_result_limit(monkeypatch):
    from execution import liquidation_scan

    calls = []

    def topic(address: str) -> str:
        return "0x" + "0" * 24 + address.removeprefix("0x").lower()

    class FakeEth:
        block_number = 30

        @staticmethod
        def get_logs(params):
            calls.append((params["fromBlock"], params["toBlock"]))
            return [
                {
                    "topics": [
                        "0xborrow",
                        "0xreserve",
                        topic("0x0000000000000000000000000000000000000001"),
                    ]
                }
            ]

    class FakeWeb3:
        def __init__(self, provider):
            self.eth = FakeEth()

        @staticmethod
        def HTTPProvider(*args, **kwargs):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return str(value)

    monkeypatch.setattr(liquidation_scan, "Web3", FakeWeb3)
    monkeypatch.setattr(liquidation_scan, "BORROW_EVENT_TOPIC", "0xborrow")

    addresses = discover_borrower_addresses(
        "https://rpc.example",
        "0xpool",
        1,
        to_block=30,
        chunk_size=10,
        limit=1,
    )

    assert addresses == ["0x0000000000000000000000000000000000000001"]
    assert calls == [(1, 10), (11, 20), (21, 30)]


def test_discover_borrower_addresses_allows_unlimited_results(monkeypatch):
    from execution import liquidation_scan

    def topic(address: str) -> str:
        return "0x" + "0" * 24 + address.removeprefix("0x").lower()

    class FakeEth:
        block_number = 10

        @staticmethod
        def get_logs(params):
            return [
                {"topics": ["0xborrow", "0xreserve", topic("0x0000000000000000000000000000000000000001")]},
                {"topics": ["0xborrow", "0xreserve", topic("0x0000000000000000000000000000000000000002")]},
            ]

    class FakeWeb3:
        def __init__(self, provider):
            self.eth = FakeEth()

        @staticmethod
        def HTTPProvider(*args, **kwargs):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return str(value)

    monkeypatch.setattr(liquidation_scan, "Web3", FakeWeb3)
    monkeypatch.setattr(liquidation_scan, "BORROW_EVENT_TOPIC", "0xborrow")

    addresses = discover_borrower_addresses(
        "https://rpc.example",
        "0xpool",
        1,
        to_block=10,
        chunk_size=10,
        limit=0,
    )

    assert addresses == [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
    ]


def test_discover_borrower_addresses_can_stop_between_chunks(monkeypatch):
    from execution import liquidation_scan
    import threading

    calls = []
    progress = []
    stop_event = threading.Event()

    def topic(address: str) -> str:
        return "0x" + "0" * 24 + address.removeprefix("0x").lower()

    class FakeEth:
        block_number = 30

        @staticmethod
        def get_logs(params):
            calls.append((params["fromBlock"], params["toBlock"]))
            return [
                {
                    "topics": [
                        "0xborrow",
                        "0xreserve",
                        topic("0x0000000000000000000000000000000000000001"),
                    ]
                }
            ]

    class FakeWeb3:
        def __init__(self, provider):
            self.eth = FakeEth()

        @staticmethod
        def HTTPProvider(*args, **kwargs):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return str(value)

    def on_progress(item):
        progress.append(item)
        stop_event.set()

    monkeypatch.setattr(liquidation_scan, "Web3", FakeWeb3)
    monkeypatch.setattr(liquidation_scan, "BORROW_EVENT_TOPIC", "0xborrow")

    addresses = discover_borrower_addresses(
        "https://rpc.example",
        "0xpool",
        1,
        to_block=30,
        chunk_size=10,
        limit=0,
        stop_event=stop_event,
        progress_callback=on_progress,
    )

    assert addresses == ["0x0000000000000000000000000000000000000001"]
    assert calls == [(1, 10)]
    assert progress[0]["discovered_count"] == 1


def test_build_liquidation_execution_plan_marks_readiness():
    plan = build_liquidation_execution_plan(
        "0x0000000000000000000000000000000000000001",
        {"health_factor": 0.98},
        {
            "collateral_symbol": "WETH",
            "debt_symbol": "USDC",
            "estimated_profit": {"net_profit_base": 12.5, "gross_profit_base": 15.0},
        },
        LiquidationScanConfig(close_factor=0.5),
    )

    assert plan["execution_ready"]
    assert plan["profitable"]
    assert plan["reason"] == "ready for execution preflight"


def test_near_threshold_healthy_account_is_not_liquidatable(monkeypatch):
    from execution import liquidation_scan

    account = "0xa845Cbe370B99AdDaB67AfE442F2cF5784d4dC29"

    def fake_fetch(pool_address, account, rpc_url):
        return {
            "account": account,
            "total_collateral_base": 347728081162567,
            "total_debt_base": 312865305356406,
            "available_borrows_base": 10521810124781,
            "current_liquidation_threshold": 9500,
            "ltv": 9300,
            "health_factor": 1.0558590915925437,
        }

    monkeypatch.setattr(liquidation_scan, "fetch_user_account_data", fake_fetch)

    rows = scan_account_health(
        [account],
        "0x794a61358D6845594F94dc1db02a252b5b4814aD",
        "https://rpc.example",
        LiquidationScanConfig(warning_health_factor=1.05, liquidation_health_factor=1.0),
    )
    plan = build_liquidation_execution_plan(
        account,
        rows[0],
        recommended_candidate=None,
        config=LiquidationScanConfig(warning_health_factor=1.05, liquidation_health_factor=1.0),
    )

    assert rows[0]["status"] == "healthy"
    assert rows[0]["health_factor"] == pytest.approx(1.0558590915925437)
    assert rows[0]["health_factor"] > 1.0
    assert plan["liquidation_ready"] is False
    assert plan["execution_ready"] is False


def test_aave_base_currency_values_are_normalized(monkeypatch):
    from execution import liquidation_scan

    monkeypatch.setenv("AAVE_BASE_CURRENCY_UNIT", "100000000")

    summary = liquidation_scan._parse_position_info(
        (
            4598,
            4748,
            0,
            7950,
            7500,
            769884161752316800,
        )
    )
    debt = liquidation_scan._parse_debt_info((10**18, 99000000, "0xToken", 47920255303018, 4748))

    assert summary["total_collateral_in_base_currency"] == pytest.approx(0.00004598)
    assert summary["total_debt_in_base_currency"] == pytest.approx(0.00004748)
    assert summary["total_debt_in_base_currency_raw"] == 4748
    assert summary["base_currency_unit"] == 100000000
    assert debt["debt_balance_in_base_currency"] == pytest.approx(0.00004748)
    assert debt["debt_balance_in_base_currency_raw"] == 4748


def test_build_liquidation_execution_payload_requires_static_preflight():
    report = {
        "account": "0x0000000000000000000000000000000000000001",
        "summary": {"status": "liquidatable"},
        "execution_plan": {"execution_ready": True},
        "recommended_candidate": {
            "collateral_asset": "0x0000000000000000000000000000000000000002",
            "debt_asset": "0x0000000000000000000000000000000000000003",
            "amount_to_pass_to_liquidation_call": 1000,
            "min_collateral_swap_out": 900,
            "estimated_profit": {"net_profit_base": 123},
        },
    }

    payload = build_liquidation_execution_payload(
        report,
        executor_address="0x0000000000000000000000000000000000000004",
        router_address="0x0000000000000000000000000000000000000005",
        deadline=123456,
    )

    assert payload["method"] == "requestLiquidation"
    assert payload["request"]["debtToCover"] == "1000"
    assert payload["request"]["minCollateralSwapOut"] == "900"
    assert payload["request"]["minProfitAmount"] == "113"
    assert payload["preflight"]["static_call_required"] is True


def test_build_liquidation_execution_payload_rejects_healthy_account():
    report = {
        "account": "0x0000000000000000000000000000000000000001",
        "summary": {"status": "healthy"},
        "execution_plan": {"execution_ready": False},
        "recommended_candidate": {
            "collateral_asset": "0x0000000000000000000000000000000000000002",
            "debt_asset": "0x0000000000000000000000000000000000000003",
            "amount_to_pass_to_liquidation_call": 1000,
            "min_collateral_swap_out": 900,
            "estimated_profit": {"net_profit_base": 123},
        },
    }

    with pytest.raises(ValueError, match="not liquidatable"):
        build_liquidation_execution_payload(
            report,
            executor_address="0x0000000000000000000000000000000000000004",
            router_address="0x0000000000000000000000000000000000000005",
            deadline=123456,
        )


def test_build_liquidation_execution_payload_requires_min_swap_output():
    report = {
        "account": "0x0000000000000000000000000000000000000001",
        "summary": {"status": "liquidatable"},
        "execution_plan": {"execution_ready": True},
        "recommended_candidate": {
            "collateral_asset": "0x0000000000000000000000000000000000000002",
            "debt_asset": "0x0000000000000000000000000000000000000003",
            "amount_to_pass_to_liquidation_call": 1000,
            "estimated_profit": {"net_profit_base": 123},
        },
    }

    with pytest.raises(ValueError, match="min_collateral_swap_out"):
        build_liquidation_execution_payload(
            report,
            executor_address="0x0000000000000000000000000000000000000004",
            router_address="0x0000000000000000000000000000000000000005",
            deadline=123456,
        )


def test_build_liquidation_execution_payload_quotes_min_swap_output(monkeypatch):
    from execution import liquidation_payload
    from web3 import Web3 as RealWeb3

    class FakeRouterFunctions:
        @staticmethod
        def getAmountsOut(amount_in, path):
            class Call:
                @staticmethod
                def call():
                    return [amount_in, 1000]

            return Call()

    class FakeRouter:
        functions = FakeRouterFunctions()

    class FakeEth:
        @staticmethod
        def contract(address, abi):
            return FakeRouter()

    class FakeWeb3:
        def __init__(self, provider):
            self.eth = FakeEth()

        @staticmethod
        def HTTPProvider(*args, **kwargs):
            return object()

        @staticmethod
        def to_checksum_address(value):
            return RealWeb3.to_checksum_address(value)

    monkeypatch.setattr(liquidation_payload, "Web3", FakeWeb3)

    report = {
        "account": "0x0000000000000000000000000000000000000001",
        "summary": {"status": "liquidatable"},
        "context": {"rpc_url": "https://rpc.example"},
        "execution_plan": {"execution_ready": True},
        "recommended_candidate": {
            "collateral_asset": "0x0000000000000000000000000000000000000002",
            "debt_asset": "0x0000000000000000000000000000000000000003",
            "max_collateral_to_liquidate": 1000,
            "amount_to_pass_to_liquidation_call": 100,
            "estimated_profit": {"net_profit_base": 123},
        },
    }

    payload = build_liquidation_execution_payload(
        report,
        executor_address="0x0000000000000000000000000000000000000004",
        router_address="0x0000000000000000000000000000000000000005",
        deadline=123456,
    )

    assert payload["request"]["minCollateralSwapOut"] == "995"
    assert payload["preflight"]["static_call_status"] == "pending"
    assert payload["dex_quote"]["quoted_amount_out"] == "1000"
