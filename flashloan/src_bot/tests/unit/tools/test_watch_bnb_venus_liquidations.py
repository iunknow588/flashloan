import json

from tools import watch_bnb_venus_liquidations as watcher


def test_watch_bnb_venus_liquidations_no_accounts_writes_empty_registry(tmp_path):
    result = watcher.main(
        [
            "--duration-seconds",
            "0",
            "--output-dir",
            str(tmp_path),
            "--accounts-file",
            str(tmp_path / "missing.json"),
            "--market-table",
            str(tmp_path / "missing_market.json"),
        ]
    )

    registry = json.loads((tmp_path / "venus_borrower_registry.json").read_text(encoding="utf-8"))
    assert result == 0
    assert registry["account_count"] == 0
    assert registry["storage_mode"] == "file_only"


def test_watch_load_accounts_normalizes_json_and_deduplicates(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {"account": "0x0000000000000000000000000000000000000001"},
                    {"account": "0X0000000000000000000000000000000000000001"},
                    {"account": "invalid"},
                ]
            }
        ),
        encoding="utf-8",
    )

    accounts = watcher._load_accounts(path)

    assert accounts == ["0x0000000000000000000000000000000000000001"]


def test_watch_bnb_venus_liquidations_splits_tiers_and_writes_round_metrics(tmp_path):
    accounts = [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
        "0x0000000000000000000000000000000000000003",
    ]
    buckets = watcher._split_accounts_by_tier(
        accounts,
        {
            "accounts": [
                {"account": accounts[0], "tier": "hot"},
                {"account": accounts[1], "tier": "warm"},
                {"account": accounts[2], "tier": "cold"},
            ]
        },
    )
    payload = {
        "generated_at": "2026-08-16T00:00:00Z",
        "execution_enabled": False,
        "summary": {"account_count": 1, "liquidatable_accounts": 1, "warning_accounts": 0, "candidate_count": 1},
        "quote_success_count": 1,
        "quote_failure_count": 0,
        "summary": {
            "account_count": 1,
            "liquidatable_accounts": 1,
            "warning_accounts": 0,
            "candidate_count": 1,
            "positive_net_profit_count": 1,
            "negative_net_profit_count": 0,
        },
        "round_duration_ms": 12.5,
        "rejected_reason_counts": {"collateral_dust": 1},
        "state": {"last_round": 1},
    }

    paths = watcher._write_outputs(tmp_path, payload)

    assert buckets["hot"] == [accounts[0]]
    assert buckets["warm"] == [accounts[1]]
    assert buckets["cold"] == [accounts[2]]
    assert "Round duration ms" in (tmp_path / "venus_liquidation_summary.md").read_text(encoding="utf-8")
    assert paths["state"].endswith("venus_watcher_state.json")
    assert paths["audit_json"].endswith("venus_file_state_audit.json")
    assert paths["audit_md"].endswith("venus_file_state_audit.md")


def test_watch_bnb_venus_liquidations_uses_avalanche_core_threshold_for_current_pools():
    accounts = [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
        "0x0000000000000000000000000000000000000003",
    ]
    current_pool = {
        "reports": [
            {"account": accounts[0], "summary": {"status": "healthy", "health_factor": 1.009}, "liquidation_candidates": []},
            {"account": accounts[1], "summary": {"status": "healthy", "health_factor": 1.05}, "liquidation_candidates": []},
            {"account": accounts[2], "summary": {"status": "healthy", "health_factor": 1.2}, "liquidation_candidates": []},
        ]
    }
    pools = watcher._current_health_pools(accounts, {"accounts": []}, current_pool)

    assert pools["core_opportunity"] == [accounts[0]]
    assert pools["high_frequency"] == [accounts[1]]
    assert pools["borrow_health"] == [accounts[2]]


def test_watch_bnb_venus_liquidations_archives_existing_outputs(tmp_path):
    output_dir = tmp_path / "data_info" / "BNB" / "liquidation"
    output_dir.mkdir(parents=True)
    latest = output_dir / "venus_liquidation_latest.json"
    summary = output_dir / "venus_liquidation_summary.md"
    latest.write_text("{}", encoding="utf-8")
    summary.write_text("# old", encoding="utf-8")

    archived = watcher._archive_existing_outputs(output_dir, label="batch1")

    assert not latest.exists()
    assert not summary.exists()
    assert archived["venus_liquidation_latest.json"].endswith("back\\BNB\\liquidation\\batch1\\venus_liquidation_latest.json") or archived[
        "venus_liquidation_latest.json"
    ].endswith("back/BNB/liquidation/batch1/venus_liquidation_latest.json")


def test_watch_current_health_pool_merges_incremental_rounds_without_losing_accounts(tmp_path):
    accounts = [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
        "0x0000000000000000000000000000000000000003",
    ]
    registry = {
        "accounts": [{"account": account, "tier": "cold"} for account in accounts],
        "scan_complete": True,
        "discovery_mode": "borrow_events_account_only",
        "storage_mode": "file_only",
    }
    first_round = {
        "round": 1,
        "generated_at": "2026-08-16T00:00:00Z",
        "block_number": 101,
        "reports": [{"account": accounts[0], "summary": {"status": "healthy", "health_factor": 1.2}, "risk": {}}],
    }
    current = watcher._merge_current_health_pool(
        tmp_path,
        first_round,
        registry,
        scanned_pools=["borrow_health"],
    )
    watcher._write_outputs(tmp_path, first_round, current_pool=current)

    second_round = {
        "round": 2,
        "generated_at": "2026-08-16T00:01:00Z",
        "block_number": 102,
        "reports": [{"account": accounts[1], "summary": {"status": "healthy", "health_factor": 1.05}, "risk": {}}],
    }
    merged = watcher._merge_current_health_pool(
        tmp_path,
        second_round,
        registry,
        scanned_pools=["high_frequency"],
    )

    reports = {row["account"]: row for row in merged["reports"]}
    assert len(reports) == 3
    assert reports[accounts[0]]["summary"]["health_factor"] == 1.2
    assert reports[accounts[1]]["summary"]["health_factor"] == 1.05
    assert reports[accounts[2]]["summary"]["status"] == "pending"
    assert merged["summary"]["scanned_account_count"] == 2
