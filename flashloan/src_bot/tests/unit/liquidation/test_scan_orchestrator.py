from types import SimpleNamespace

from bot.liquidation.scan_orchestrator import build_account_health_reports, run_borrow_health_scan, scan_progress


def _config():
    return SimpleNamespace(watch_health_factor=1.2, min_operator_net_profit_usd=0.5)


def test_build_account_health_reports_normalizes_scanner_rows():
    reports = build_account_health_reports([
        {
            "account": "0x1",
            "health_factor": 0.9,
            "status": "liquidatable",
            "health_factor_band": "danger",
            "liquidation_candidates": [{"debt": 1}, {"debt": 2}],
            "total_collateral_in_base_currency": 100,
            "total_debt_base": 80,
            "liquidation_profit": {"net_usd": 2},
        }
    ])

    assert reports == [
        {
            "account": "0x1",
            "summary": {
                "health_factor": 0.9,
                "status": "liquidatable",
                "health_factor_band": "danger",
                "candidate_count": 2,
                "total_collateral_base": 100,
                "total_debt_base": 80,
            },
            "liquidation_profit": {"net_usd": 2},
        }
    ]


def test_scan_progress_includes_rpc_only_when_present():
    progress = scan_progress(
        source_account_count=3,
        selected_account_count=2,
        scanned_count=1,
        scan_selection={"strategy": "core"},
        rpc_url="https://rpc.example",
    )

    assert progress == {
        "account_count": 3,
        "selected_account_count": 2,
        "scanned_count": 1,
        "scan_strategy": "core",
        "rpc_url": "https://rpc.example",
    }


def test_run_borrow_health_scan_selects_accounts_syncs_rows_and_marks_finished():
    calls = {"progress": [], "marked": []}
    rows = [{"account": "0x2", "health_factor": 0.8, "status": "liquidatable"}]

    result = run_borrow_health_scan(
        "postgres://db",
        _config(),
        force=True,
        market_kwargs={"market_id": "avalanche-aave-v3", "chain_id": 43114},
        ensure_schema=lambda database_url: calls.setdefault("schema", database_url),
        load_accounts=lambda database_url, **kwargs: ["0x1", "0x2", "0x1"],
        unique_accounts=lambda accounts: list(dict.fromkeys(accounts)),
        select_accounts=lambda database_url, accounts, config, **kwargs: {
            "selected_accounts": ["0x2"],
            "strategy": "borrow_health_full",
            "included_tiers": ["borrow_health"],
        },
        rpc_urls=lambda: ["https://rpc.example"],
        pool_address=lambda: "0xpool",
        scan_health=lambda accounts, pool, rpc_url, config: rows,
        block_number=lambda rpc_url: 123,
        sync_pool=lambda database_url, scan_rows, watch_hf, **kwargs: {"entered_count": len(scan_rows), "exited_count": 0},
        prune_accounts=lambda database_url, **kwargs: calls.setdefault("pruned", kwargs["retained_days"]),
        retention_days=lambda: 30,
        mark_selection_finished=lambda selection: calls["marked"].append(selection["strategy"]),
        scan_error_message=lambda exc: str(exc),
        progress=lambda value: calls["progress"].append(value),
    )

    assert result.source_account_count == 2
    assert result.selected_accounts == ["0x2"]
    assert result.rows == rows
    assert result.rpc_url == "https://rpc.example"
    assert result.sync_result == {"entered_count": 1, "exited_count": 0}
    assert result.block_number == 123
    assert calls["marked"] == ["borrow_health_full"]
    assert calls["progress"][-1]["scanned_count"] == 1


def test_run_borrow_health_scan_falls_back_to_next_rpc_after_error():
    attempts = []

    def scan_health(accounts, pool, rpc_url, config):
        attempts.append(rpc_url)
        if rpc_url == "bad-rpc":
            raise RuntimeError("rpc failed")
        return [{"account": "0x1", "health_factor": 0.9}]

    result = run_borrow_health_scan(
        "postgres://db",
        _config(),
        force=False,
        market_kwargs={},
        ensure_schema=lambda database_url: None,
        load_accounts=lambda database_url, **kwargs: ["0x1"],
        unique_accounts=lambda accounts: accounts,
        select_accounts=lambda database_url, accounts, config, **kwargs: {"selected_accounts": accounts, "strategy": "fallback"},
        rpc_urls=lambda: ["bad-rpc", "good-rpc"],
        pool_address=lambda: "0xpool",
        scan_health=scan_health,
        block_number=lambda rpc_url: 456,
        sync_pool=lambda *args, **kwargs: {},
        prune_accounts=lambda *args, **kwargs: None,
        retention_days=lambda: 7,
        mark_selection_finished=lambda selection: None,
        scan_error_message=lambda exc: f"safe:{exc}",
    )

    assert attempts == ["bad-rpc", "good-rpc"]
    assert result.rpc_url == "good-rpc"
    assert result.error is None


def test_run_borrow_health_scan_returns_empty_pool_error_without_rpc_scan():
    result = run_borrow_health_scan(
        "postgres://db",
        _config(),
        force=False,
        market_kwargs={},
        ensure_schema=lambda database_url: None,
        load_accounts=lambda database_url, **kwargs: [],
        unique_accounts=lambda accounts: [],
        select_accounts=lambda database_url, accounts, config, **kwargs: {"selected_accounts": [], "strategy": "empty"},
        rpc_urls=lambda: ["unused"],
        pool_address=lambda: "0xpool",
        scan_health=lambda *args: [],
        block_number=lambda rpc_url: None,
        sync_pool=lambda *args, **kwargs: {},
        prune_accounts=lambda *args, **kwargs: None,
        retention_days=lambda: 7,
        mark_selection_finished=lambda selection: None,
        scan_error_message=lambda exc: str(exc),
    )

    assert result.selected_accounts == []
    assert result.error == "database liquidation account table is empty"
    assert result.rpc_url is None
