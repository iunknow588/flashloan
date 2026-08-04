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
    incremental_scan_account_groups,
    load_account_addresses,
    scan_account_health,
    watched_health_rows,
    split_candidate_accounts,
)
from execution.liquidation_payload import build_liquidation_execution_payload
from tools.benchmark_liquidation_scan import run_synthetic_benchmark


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


def test_build_liquidation_candidates_allows_same_asset_collateral_and_debt(monkeypatch):
    from execution import liquidation_scan

    calls = []

    class FakeProvider:
        class functions:
            @staticmethod
            def getLiquidationInfo(user, collateral, debt):
                calls.append((collateral, debt))

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
        {
            "token_address": "0xUSDC",
            "symbol": "USDC",
            "usage_as_collateral_enabled": True,
            "current_a_token_balance": 500,
            "current_stable_debt": 0,
            "current_variable_debt": 300,
            "decimals": 6,
            "oracle_price": 1,
        },
    ]

    candidates = build_liquidation_candidates(
        "https://rpc.example",
        "0xabc",
        positions,
        "0xdef",
        LiquidationScanConfig(close_factor=0.5),
    )

    assert calls == [("0xUSDC", "0xUSDC")]
    assert candidates[0]["collateral_asset"] == "0xUSDC"
    assert candidates[0]["debt_asset"] == "0xUSDC"


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


def test_scan_account_health_uses_parallel_workers(monkeypatch):
    from execution import liquidation_scan

    calls = []

    def fake_fetch(pool_address, account, rpc_url):
        calls.append(account)
        return {
            "account": account,
            "total_collateral_base": 1200,
            "total_debt_base": 1000,
            "available_borrows_base": 0,
            "current_liquidation_threshold": 8000,
            "ltv": 7500,
            "health_factor": 0.98 if account.endswith("1") else 1.02,
        }

    monkeypatch.setattr(liquidation_scan, "fetch_user_account_data", fake_fetch)
    accounts = [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
    ]

    rows = scan_account_health(
        accounts,
        "0x0000000000000000000000000000000000000003",
        "https://rpc.example",
        LiquidationScanConfig(parallel_workers=2, max_candidates=10),
    )

    assert calls == accounts
    assert [row["status"] for row in rows] == ["liquidatable", "warning"]


def test_scan_account_health_uses_multicall_batch(monkeypatch):
    from execution import liquidation_scan

    accounts = [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
    ]
    calls = []

    def fake_batch(pool_address, batch_accounts, rpc_url, multicall3_address, batch_size):
        calls.append((list(batch_accounts), multicall3_address, batch_size))
        return {
            accounts[0]: {
                "account": accounts[0],
                "total_collateral_base": 1200,
                "total_debt_base": 1000,
                "available_borrows_base": 0,
                "current_liquidation_threshold": 8000,
                "ltv": 7500,
                "health_factor": 0.98,
                "account_data_source": "multicall3",
            },
            accounts[1]: {
                "account": accounts[1],
                "total_collateral_base": 1500,
                "total_debt_base": 900,
                "available_borrows_base": 0,
                "current_liquidation_threshold": 8000,
                "ltv": 7500,
                "health_factor": 1.02,
                "account_data_source": "multicall3",
            },
        }

    def fail_fetch(pool_address, account, rpc_url):
        raise AssertionError("single-account fetch should not be used when multicall has data")

    monkeypatch.setattr(liquidation_scan, "fetch_user_account_data_batch", fake_batch)
    monkeypatch.setattr(liquidation_scan, "fetch_user_account_data", fail_fetch)

    rows = scan_account_health(
        accounts,
        "0x0000000000000000000000000000000000000003",
        "https://rpc.example",
        LiquidationScanConfig(
            parallel_workers=1,
            batch_size=100,
            multicall3_address="0xcA11bde05977b3631167028862bE2a173976CA11",
        ),
    )

    assert calls == [(accounts, "0xcA11bde05977b3631167028862bE2a173976CA11", 100)]
    assert [row["account_data_source"] for row in rows] == ["multicall3", "multicall3"]
    assert [row["status"] for row in rows] == ["liquidatable", "warning"]


def test_scan_account_health_falls_back_when_multicall_fails(monkeypatch):
    from execution import liquidation_scan

    calls = []

    def fake_batch(*args, **kwargs):
        raise RuntimeError("multicall unavailable")

    def fake_fetch(pool_address, account, rpc_url):
        calls.append(account)
        return {
            "account": account,
            "total_collateral_base": 1200,
            "total_debt_base": 1000,
            "available_borrows_base": 0,
            "current_liquidation_threshold": 8000,
            "ltv": 7500,
            "health_factor": 0.98,
        }

    accounts = [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
    ]
    monkeypatch.setattr(liquidation_scan, "fetch_user_account_data_batch", fake_batch)
    monkeypatch.setattr(liquidation_scan, "fetch_user_account_data", fake_fetch)

    rows = scan_account_health(
        accounts,
        "0x0000000000000000000000000000000000000003",
        "https://rpc.example",
        LiquidationScanConfig(
            parallel_workers=1,
            batch_size=100,
            multicall3_address="0xcA11bde05977b3631167028862bE2a173976CA11",
        ),
    )

    assert calls == accounts
    assert [row["status"] for row in rows] == ["liquidatable", "liquidatable"]


def test_scan_account_health_5000_account_benchmark_stays_under_30_seconds():
    result = run_synthetic_benchmark(account_count=5000, batch_size=100)

    assert result["account_count"] == 5000
    assert result["batch_count"] == 50
    assert result["elapsed_seconds"] < 30.0
    assert result["liquidatable_count"] > 0
    assert result["warning_count"] > 0


def test_scan_account_health_keeps_one_account_failure_isolated(monkeypatch):
    from execution import liquidation_scan

    def fake_fetch(pool_address, account, rpc_url):
        if account.endswith("2"):
            raise RuntimeError("rpc failure")
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
        [
            "0x0000000000000000000000000000000000000001",
            "0x0000000000000000000000000000000000000002",
        ],
        "0x0000000000000000000000000000000000000003",
        "https://rpc.example",
        LiquidationScanConfig(parallel_workers=2),
    )

    assert rows[0]["status"] == "liquidatable"
    assert rows[1]["status"] == "error"
    assert rows[1]["error"] == "rpc failure"


def test_scan_account_health_redacts_fetch_errors(monkeypatch):
    from execution import liquidation_scan

    rpc_url = "https://rpc.example/path?token=abc123"
    private_key = "0x" + "4" * 64
    monkeypatch.setenv("AVALANCHE_RPC_URL", rpc_url)

    def fake_fetch(pool_address, account, rpc_url_arg):
        raise RuntimeError(f"fetch failed: {rpc_url} private_key={private_key}")

    monkeypatch.setattr(liquidation_scan, "fetch_user_account_data", fake_fetch)
    rows = scan_account_health(
        ["0x0000000000000000000000000000000000000001"],
        "0x0000000000000000000000000000000000000003",
        rpc_url,
        LiquidationScanConfig(parallel_workers=1),
    )

    error = rows[0]["error"]
    assert rows[0]["status"] == "error"
    assert rpc_url not in error
    assert private_key not in error
    assert "abc123" not in error
    assert "[REDACTED]" in error


def test_incremental_scan_keeps_watch_accounts_and_full_scan_fallback():
    accounts = [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
        "0x0000000000000000000000000000000000000003",
    ]
    previous_rows = [
        {"account": accounts[0], "health_factor": 1.2},
        {"account": accounts[1], "health_factor": 2.1},
    ]

    watch_only = incremental_scan_account_groups(accounts, previous_rows, full_scan_due=False)
    full_scan = incremental_scan_account_groups(accounts, previous_rows, full_scan_due=True)

    assert watch_only["scan_accounts"] == [accounts[0]]
    assert watch_only["strategy"] == ["watch_high_frequency"]
    assert full_scan["scan_accounts"] == accounts
    assert accounts[2] in full_scan["full_scan_accounts"]
    assert "full_low_frequency" in full_scan["strategy"]


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


def test_build_liquidation_execution_plan_requires_profit_above_one_usd():
    plan = build_liquidation_execution_plan(
        "0x0000000000000000000000000000000000000001",
        {"health_factor": 0.98},
        {
            "collateral_symbol": "WETH",
            "debt_symbol": "USDC",
            "estimated_profit": {"net_profit_base": 1.0, "gross_profit_base": 3.0},
        },
        LiquidationScanConfig(min_operator_net_profit_usd=1.0),
    )

    assert plan["liquidation_ready"] is True
    assert plan["profitable"] is False
    assert plan["execution_ready"] is False
    assert "not above 1.00 USD" in plan["reason"]


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
            "debt_decimals": 6,
            "debt_price": 1,
            "amount_to_pass_to_liquidation_call": 1000,
            "min_collateral_swap_out": 900,
            "swap_path": [
                "0x0000000000000000000000000000000000000002",
                "0x0000000000000000000000000000000000000003",
            ],
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
    assert payload["request"]["minProfitAmount"] == "122999990"
    assert payload["request"]["gasLimit"] == "0"
    assert payload["preflight"]["min_profit_consistency"]["consistent"] is True
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


def test_build_liquidation_execution_payload_rejects_missing_candidate_before_plan_check():
    report = {
        "account": "0x0000000000000000000000000000000000000001",
        "summary": {"status": "liquidatable"},
        "execution_plan": {"execution_ready": False},
        "recommended_candidate": None,
    }

    with pytest.raises(ValueError, match="no executable liquidation candidate"):
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
            "debt_price": 1,
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
            "debt_price": 1,
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
