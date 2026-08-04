from execution.liquidation_scan import LiquidationScanConfig


def _reset_scan_cache(scan):
    for key in (
        "borrow_pool_payload",
        "borrow_pool_updated_at",
        "last_core_scan_monotonic",
        "last_high_frequency_scan_monotonic",
        "last_borrow_health_scan_monotonic",
        "last_scan_strategy",
    ):
        scan.LIQUIDATION_SCAN_CACHE.pop(key, None)
    scan.LIQUIDATION_SCAN_CACHE.update({"running": False, "stage": "idle", "finished_at": None})


def _rows(*accounts):
    return [{"account": account} for account in accounts]


def test_liquidation_core_rows_with_execution_attaches_execution_summary(monkeypatch):
    from web import control_panel_liquidation_scan as scan

    monkeypatch.setattr(
        scan,
        "db_load_liquidation_core_opportunity_pool",
        lambda database_url, limit, offset=0: [
            {
                "account": "0xready",
                "auto_execution_blocked": False,
                "best_collateral_asset": "WAVAX",
                "best_debt_asset": "USDC",
                "quote_viable": True,
                "static_call_status": "passed",
                "estimated_operator_net_profit_usd": 12.5,
                "blocked_reasons": [],
                "profit_assessment": {"above_auto_profit_threshold": True},
            },
            {
                "account": "0xmanual",
                "auto_execution_blocked": True,
                "best_collateral_asset": "WAVAX",
                "best_debt_asset": "USDC",
                "quote_viable": False,
                "static_call_status": "pending",
                "estimated_operator_net_profit_usd": 0.5,
                "blocked_reasons": ["profit_below_minimum"],
                "profit_assessment": {"above_auto_profit_threshold": False},
            },
            {
                "account": "0xstale",
                "auto_execution_blocked": False,
                "best_collateral_asset": "WAVAX",
                "best_debt_asset": "USDC",
                "quote_viable": True,
                "static_call_status": "pending",
                "estimated_operator_net_profit_usd": 3.0,
                "blocked_reasons": [],
                "last_scanned_at": "2026-08-03T01:00:00+00:00",
                "profit_assessment": {"above_auto_profit_threshold": True},
            },
        ],
    )
    attempts = [
                {
                    "account": "0xready",
                    "state": "confirmed_success",
                "execution_phase": "confirmed_success",
                "tx_hash": "0xabc",
                    "error": None,
                    "created_at": "2026-08-03T00:00:00+00:00",
                },
                {
                    "account": "0xstale",
                    "state": "submission_failed",
                    "execution_phase": "submission_failed",
                    "tx_hash": None,
                    "error": "old failure",
                    "created_at": "2026-08-03T00:00:00+00:00",
                }
            ]
    monkeypatch.setattr(
        scan,
        "db_load_latest_liquidation_execution_attempts_for_accounts",
        lambda database_url, accounts: attempts,
    )
    monkeypatch.setattr(
        scan,
        "db_load_recent_liquidation_execution_attempts",
        lambda database_url, limit: attempts,
    )

    rows = scan.liquidation_core_rows_with_execution("postgresql://example", limit=20)

    assert rows[0]["execution"]["value_state"] == "worth_executing"
    assert rows[0]["execution"]["execution_status"] == "preflight_passed"
    assert rows[0]["execution"]["execution_result"] == "confirmed_success"
    assert rows[0]["execution"]["latest_attempt"]["tx_hash"] == "0xabc"
    assert rows[1]["execution"]["value_state"] == "manual_test_under_1u"
    assert rows[1]["execution"]["execution_result"] == "not_submitted"
    assert rows[2]["execution"]["execution_result"] == "not_submitted"
    assert rows[2]["execution"]["latest_attempt"]["stale"] is True


def test_select_scan_accounts_uses_core_when_only_base_cycle_is_due(monkeypatch):
    from web import control_panel_liquidation_scan as scan

    _reset_scan_cache(scan)
    monkeypatch.setattr(scan.time, "monotonic", lambda: 1000.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_core_scan_monotonic", 998.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_high_frequency_scan_monotonic", 999.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_borrow_health_scan_monotonic", 999.0)
    monkeypatch.setattr(scan, "db_load_liquidation_core_opportunity_pool", lambda database_url, limit=100, offset=0: _rows("0x2"))
    monkeypatch.setattr(scan, "db_load_liquidation_high_frequency_pool", lambda database_url, limit=100, offset=0: _rows("0x3", "0x2"))

    selection = scan.select_liquidation_scan_accounts(
        "postgresql://example",
        ["0x1", "0x2", "0x3"],
        LiquidationScanConfig(
            core_opportunity_refresh_seconds=1,
            high_frequency_refresh_seconds=300,
            borrow_health_refresh_seconds=1800,
        ),
    )

    assert selection["strategy"] == "core_opportunity_refresh"
    assert selection["selected_accounts"] == ["0x2"]
    assert selection["selected_account_count"] == 1
    assert selection["account_count"] == 3
    assert selection["core_due"] is True
    assert selection["high_frequency_due"] is False
    assert selection["borrow_health_due"] is False


def test_select_scan_accounts_promotes_to_high_frequency_when_due(monkeypatch):
    from web import control_panel_liquidation_scan as scan

    _reset_scan_cache(scan)
    monkeypatch.setattr(scan.time, "monotonic", lambda: 1000.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_core_scan_monotonic", 998.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_high_frequency_scan_monotonic", 650.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_borrow_health_scan_monotonic", 999.0)
    monkeypatch.setattr(scan, "db_load_liquidation_core_opportunity_pool", lambda database_url, limit=100, offset=0: _rows("0x2"))
    monkeypatch.setattr(scan, "db_load_liquidation_high_frequency_pool", lambda database_url, limit=100, offset=0: _rows("0x3", "0x2"))

    selection = scan.select_liquidation_scan_accounts(
        "postgresql://example",
        ["0x1", "0x2", "0x3"],
        LiquidationScanConfig(
            core_opportunity_refresh_seconds=1,
            high_frequency_refresh_seconds=300,
            borrow_health_refresh_seconds=1800,
        ),
    )

    assert selection["strategy"] == "high_frequency_refresh"
    assert selection["selected_accounts"] == ["0x2", "0x3"]
    assert selection["included_tiers"] == ["core_opportunity", "high_frequency"]


def test_select_scan_accounts_promotes_to_full_borrow_health_when_due(monkeypatch):
    from web import control_panel_liquidation_scan as scan

    _reset_scan_cache(scan)
    monkeypatch.setattr(scan.time, "monotonic", lambda: 2000.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_core_scan_monotonic", 1999.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_high_frequency_scan_monotonic", 1999.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_borrow_health_scan_monotonic", 100.0)
    monkeypatch.setattr(scan, "db_load_liquidation_core_opportunity_pool", lambda database_url, limit=100, offset=0: _rows("0x2"))
    monkeypatch.setattr(scan, "db_load_liquidation_high_frequency_pool", lambda database_url, limit=100, offset=0: _rows("0x3"))

    selection = scan.select_liquidation_scan_accounts(
        "postgresql://example",
        ["0x1", "0x2", "0x3"],
        LiquidationScanConfig(
            core_opportunity_refresh_seconds=1,
            high_frequency_refresh_seconds=300,
            borrow_health_refresh_seconds=1800,
        ),
    )

    assert selection["strategy"] == "borrow_health_full"
    assert selection["selected_accounts"] == ["0x1", "0x2", "0x3"]
    assert selection["included_tiers"] == ["core_opportunity", "high_frequency", "borrow_health"]


def test_select_scan_accounts_falls_back_when_core_pool_is_empty(monkeypatch):
    from web import control_panel_liquidation_scan as scan

    _reset_scan_cache(scan)
    monkeypatch.setattr(scan.time, "monotonic", lambda: 1000.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_core_scan_monotonic", 998.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_high_frequency_scan_monotonic", 999.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_borrow_health_scan_monotonic", 999.0)
    monkeypatch.setattr(scan, "db_load_liquidation_core_opportunity_pool", lambda database_url, limit=100, offset=0: [])
    monkeypatch.setattr(scan, "db_load_liquidation_high_frequency_pool", lambda database_url, limit=100, offset=0: _rows("0x3"))

    selection = scan.select_liquidation_scan_accounts(
        "postgresql://example",
        ["0x1", "0x2", "0x3"],
        LiquidationScanConfig(),
    )

    assert selection["strategy"] == "fallback_high_frequency"
    assert selection["selected_accounts"] == ["0x3"]


def test_select_scan_accounts_force_runs_full_unique_account_pool(monkeypatch):
    from web import control_panel_liquidation_scan as scan

    _reset_scan_cache(scan)
    monkeypatch.setattr(scan.time, "monotonic", lambda: 1000.0)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_core_scan_monotonic", 999.9)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_high_frequency_scan_monotonic", 999.9)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_borrow_health_scan_monotonic", 999.9)
    monkeypatch.setattr(scan, "db_load_liquidation_core_opportunity_pool", lambda database_url, limit=100, offset=0: _rows("0x2"))
    monkeypatch.setattr(scan, "db_load_liquidation_high_frequency_pool", lambda database_url, limit=100, offset=0: _rows("0x3"))

    selection = scan.select_liquidation_scan_accounts(
        "postgresql://example",
        ["0x1", "0x2", "0x1", "", "0x3"],
        LiquidationScanConfig(),
        force=True,
    )

    assert selection["strategy"] == "borrow_health_full"
    assert selection["selected_accounts"] == ["0x1", "0x2", "0x3"]
    assert selection["account_count"] == 3
    assert selection["borrow_health_due"] is True


def test_mark_scan_selection_finished_updates_only_included_tiers(monkeypatch):
    from web import control_panel_liquidation_scan as scan

    _reset_scan_cache(scan)
    monkeypatch.setattr(scan.time, "monotonic", lambda: 1234.5)

    scan._mark_liquidation_scan_selection_finished(
        {"included_tiers": ["core_opportunity", "high_frequency"]}
    )

    assert scan.LIQUIDATION_SCAN_CACHE["last_core_scan_monotonic"] == 1234.5
    assert scan.LIQUIDATION_SCAN_CACHE["last_high_frequency_scan_monotonic"] == 1234.5
    assert "last_borrow_health_scan_monotonic" not in scan.LIQUIDATION_SCAN_CACHE


def test_borrow_pool_scan_cooldown_prefers_core_refresh_interval(monkeypatch):
    from web import control_panel_liquidation_scan as scan

    monkeypatch.setattr(scan, "liquidation_scan_interval_seconds", lambda: 300.0)

    assert scan.liquidation_borrow_pool_scan_cooldown_seconds(
        LiquidationScanConfig(core_opportunity_refresh_seconds=1.0)
    ) == 1.0


def test_background_refresh_loop_uses_tiered_scan_not_forced_full_scan(monkeypatch):
    from web import control_panel_liquidation_scan as scan

    _reset_scan_cache(scan)
    captured_forces = []
    captured_waits = []

    class FakeStop:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            captured_waits.append(seconds)
            self.stopped = True

    class FakeThread:
        def __init__(self, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.started = False

        @staticmethod
        def is_alive():
            return False

        def start(self):
            self.started = True
            self.target()

    monkeypatch.setattr(scan, "LIQUIDATION_REFRESH_THREAD", None)
    monkeypatch.setattr(scan, "LIQUIDATION_REFRESH_STOP", FakeStop())
    monkeypatch.setattr(scan.threading, "Thread", FakeThread)
    monkeypatch.setattr(scan, "liquidation_background_refresh_enabled", lambda: True)
    monkeypatch.setattr(scan, "discover_and_sync_liquidation_accounts", lambda force_full=False: {"force_full": force_full})
    monkeypatch.setattr(scan, "liquidation_borrow_pool_scan_payload", lambda force=False: captured_forces.append(force) or {})
    monkeypatch.setattr(scan, "liquidation_borrow_pool_scan_cooldown_seconds", lambda: 1.0)
    monkeypatch.setattr(scan, "liquidation_backfill_interval_seconds", lambda: 999999.0)
    monkeypatch.setattr(scan.time, "monotonic", lambda: 1000.0)
    monkeypatch.setitem(scan.LIQUIDATION_DISCOVERY_CACHE, "last_backfill_monotonic", 999.0)

    scan.start_liquidation_refresh_loop()

    assert captured_forces == [False]
    assert captured_waits == [1.0]


def test_borrow_pool_scan_payload_scans_only_selected_core_accounts(monkeypatch):
    from web import control_panel_liquidation_context as context

    scan = context.liquidation_scan_module
    scan_payload = context._scan_liquidation_borrow_pool_scan_payload

    _reset_scan_cache(scan)
    now = scan.time.monotonic()
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_core_scan_monotonic", now - 2)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_high_frequency_scan_monotonic", now - 1)
    monkeypatch.setitem(scan.LIQUIDATION_SCAN_CACHE, "last_borrow_health_scan_monotonic", now - 1)
    monkeypatch.setattr(scan, "database_url_or_none", lambda: "postgresql://example")
    class FakeLock:
        def __init__(self):
            self.released = False

        @staticmethod
        def acquire(blocking=False):
            return True

        def release(self):
            self.released = True

    monkeypatch.setattr(scan, "LIQUIDATION_SCAN_LOCK", FakeLock())
    monkeypatch.setattr(scan, "ensure_database_schema", lambda database_url: None)
    monkeypatch.setattr(scan, "db_load_liquidation_accounts", lambda database_url: ["0x1", "0x2", "0x3"])
    monkeypatch.setattr(scan, "db_load_liquidation_core_opportunity_pool", lambda database_url, limit=100, offset=0: _rows("0x2"))
    monkeypatch.setattr(scan, "db_load_liquidation_high_frequency_pool", lambda database_url, limit=100, offset=0: _rows("0x3"))
    monkeypatch.setattr(
        scan,
        "liquidation_scan_config",
        lambda: LiquidationScanConfig(
            core_opportunity_refresh_seconds=1,
            high_frequency_refresh_seconds=300,
            borrow_health_refresh_seconds=1800,
        ),
    )
    monkeypatch.setattr(scan, "aave_rpc_urls", lambda: ["http://rpc.example"])
    scanned_accounts = []

    def fake_scan_account_health(accounts, pool_address, rpc_url, config):
        scanned_accounts.extend(accounts)
        return [{"account": account, "health_factor": 0.99, "total_debt_base": 1000} for account in accounts]

    monkeypatch.setattr(scan, "scan_account_health", fake_scan_account_health)
    monkeypatch.setattr(
        scan,
        "db_sync_liquidation_borrow_health_pool",
        lambda *args, **kwargs: {"entered_count": 1, "exited_count": 0},
    )
    monkeypatch.setattr(scan, "db_prune_liquidation_accounts", lambda *args, **kwargs: None)
    monkeypatch.setattr(scan, "db_load_liquidation_borrow_health_pool", lambda database_url, limit=20, offset=0: [])
    class FakeWeb3:
        @staticmethod
        def HTTPProvider(url, request_kwargs=None):
            return object()

        def __init__(self, provider):
            self.eth = type("Eth", (), {"block_number": 123})()

    monkeypatch.setattr(scan, "Web3", FakeWeb3)
    monkeypatch.setattr(
        scan,
        "liquidation_pool_tier_payload",
        lambda database_url, limit, high_page=1, core_page=1: {
            "borrow_health_count": 1,
            "high_frequency_rows": [],
            "core_opportunity_rows": [],
            "high_frequency_count": 1,
            "core_opportunity_count": 1,
            "pagination": {
                "high_frequency": {"page": 1, "page_size": 20, "total_count": 1, "page_count": 1},
                "core_opportunity": {"page": 1, "page_size": 20, "total_count": 1, "page_count": 1},
            },
        },
    )
    captured_batches = []

    def fake_record_batch(database_url, **kwargs):
        captured_batches.append(kwargs)
        return {"id": 1, **kwargs}

    monkeypatch.setattr(scan, "db_record_liquidation_borrow_health_scan_batch", fake_record_batch)
    monkeypatch.setattr(scan, "db_load_liquidation_scan_config_library", lambda database_url, limit=20: [])
    monkeypatch.setattr(scan, "liquidation_account_tier_summary", lambda: {"active_count": 3, "hot_count": 1, "warm_count": 1, "cold_count": 1})

    payload = scan_payload(force=False, page_size=20)

    assert scanned_accounts == ["0x2"]
    assert payload["summary"]["scan_strategy"] == "core_opportunity_refresh"
    assert payload["summary"]["scan_response_source"] == "chain_scan"
    assert payload["summary"]["manual_force_scan"] is False
    assert payload["summary"]["source_account_count"] == 3
    assert payload["summary"]["selected_account_count"] == 1
    assert payload["summary"]["scanned_account_count"] == 1
    assert captured_batches[0]["account_count"] == 3
    assert captured_batches[0]["scanned_count"] == 1
    assert captured_batches[0]["metadata"]["scan_strategy"]["strategy"] == "core_opportunity_refresh"
