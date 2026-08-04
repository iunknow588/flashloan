from runtime.liquidation_engine import (
    LiquidationEngine,
    LiquidationEngineConfig,
    LiquidationEngineDependencies,
)


def _deps(
    *,
    records,
    submitted,
    controls=None,
    prices=None,
    events=None,
    affected=None,
    static_passed=True,
    blocked_reasons=None,
):
    price_iter = iter(prices or [])
    event_iter = iter(events or [])

    def build_payload(account):
        return {
            "account": account,
            "request": {"user": account, "debtToCover": "1000"},
            "preflight": {"static_call_required": True},
            "blocked_reasons": [],
        }

    def simulate(payload):
        return {
            **payload,
            "preflight": {
                "static_call_required": True,
                "static_call_status": "passed" if static_passed else "error",
                "static_call_passed": static_passed,
                "static_call_error": None if static_passed else "execution reverted",
            },
            "blocked_reasons": list(blocked_reasons or ([] if static_passed else ["static_call_failed"])),
        }

    def submit(payload):
        submitted.append(payload["account"])
        return {**payload, "tx_hash": "0xabc", "receipt": {"status": 1}}

    def record_attempt(**kwargs):
        records.append(kwargs)

    def load_prices():
        return next(price_iter)

    def load_events():
        return next(event_iter)

    return LiquidationEngineDependencies(
        load_accounts=lambda: ["0x1"],
        build_payload=build_payload,
        simulate_static_call=simulate,
        submit=submit,
        record_attempt=record_attempt,
        load_controls=lambda: controls or {"auto_pause_active": False, "circuit_breaker_level": 0},
        load_price_snapshot=load_prices if prices is not None else None,
        load_price_events=load_events if events is not None else None,
        load_affected_accounts=(lambda assets: affected or []) if affected is not None else None,
    )


def test_engine_config_from_env_uses_safe_defaults_for_invalid_numeric_values(monkeypatch):
    monkeypatch.setenv("LIQUIDATION_ENGINE_POLL_SECONDS", "bad")
    monkeypatch.setenv("LIQUIDATION_EVENT_POLL_SECONDS", "bad")
    monkeypatch.setenv("LIQUIDATION_PRICE_TRIGGER_BPS", "bad")
    monkeypatch.setenv("LIQUIDATION_ENGINE_MAX_ACCOUNTS", "bad")
    monkeypatch.delenv("LIQUIDATION_AUTO_EXECUTE", raising=False)
    monkeypatch.delenv("LIQUIDATION_MANUAL_TEST_COMPLETED", raising=False)

    config = LiquidationEngineConfig.from_env()

    assert config.poll_interval_seconds == 30
    assert config.event_poll_interval_seconds == 5
    assert config.price_change_threshold_bps == 100
    assert config.max_accounts_per_tick == 100
    assert config.auto_execute_requested is True
    assert config.manual_test_completed is True
    assert config.auto_execute is True


def test_engine_config_from_env_observe_mode_when_manual_test_is_explicitly_false(monkeypatch):
    monkeypatch.setenv("LIQUIDATION_AUTO_EXECUTE", "true")
    monkeypatch.setenv("LIQUIDATION_MANUAL_TEST_COMPLETED", "false")

    config = LiquidationEngineConfig.from_env()

    assert config.auto_execute_requested is True
    assert config.manual_test_completed is False
    assert config.auto_execute is False


def test_engine_config_from_env_allows_auto_execute_only_after_manual_test(monkeypatch):
    monkeypatch.setenv("LIQUIDATION_AUTO_EXECUTE", "true")
    monkeypatch.setenv("LIQUIDATION_MANUAL_TEST_COMPLETED", "true")

    config = LiquidationEngineConfig.from_env()

    assert config.auto_execute_requested is True
    assert config.manual_test_completed is True
    assert config.auto_execute is True


def test_engine_observe_mode_runs_static_call_without_submit():
    records = []
    submitted = []
    engine = LiquidationEngine(
        _deps(records=records, submitted=submitted),
        LiquidationEngineConfig(auto_execute=False),
    )

    result = engine.run_once()

    assert result["mode"] == "observe"
    assert result["processed"][0]["state"] == "observed"
    assert submitted == []
    assert records[0]["state"] == "static_call_passed"


def test_engine_auto_mode_submits_after_static_call_gate_passes():
    records = []
    submitted = []
    engine = LiquidationEngine(
        _deps(records=records, submitted=submitted),
        LiquidationEngineConfig(auto_execute=True),
    )

    result = engine.run_once()

    assert result["mode"] == "auto"
    assert result["processed"][0]["state"] == "confirmed_success"
    assert submitted == ["0x1"]
    assert [row["state"] for row in records] == ["static_call_passed", "confirmed_success"]


def test_engine_auto_mode_defers_fork_simulation_required_to_submitter():
    records = []
    submitted = []
    engine = LiquidationEngine(
        _deps(records=records, submitted=submitted, blocked_reasons=["fork_simulation_required"]),
        LiquidationEngineConfig(auto_execute=True),
    )

    result = engine.run_once()

    assert result["processed"][0]["state"] == "confirmed_success"
    assert submitted == ["0x1"]
    assert [row["state"] for row in records] == ["static_call_passed", "confirmed_success"]


def test_engine_auto_mode_blocks_failed_fork_simulation():
    records = []
    submitted = []
    engine = LiquidationEngine(
        _deps(records=records, submitted=submitted, blocked_reasons=["fork_simulation_failed"]),
        LiquidationEngineConfig(auto_execute=True),
    )

    result = engine.run_once()

    assert result["processed"][0]["state"] == "submission_blocked"
    assert result["processed"][0]["blocked_reasons"] == ["fork_simulation_failed"]
    assert submitted == []
    assert [row["state"] for row in records] == ["static_call_passed", "submission_blocked"]


def test_engine_blocks_submit_when_static_call_fails():
    records = []
    submitted = []
    engine = LiquidationEngine(
        _deps(records=records, submitted=submitted, static_passed=False),
        LiquidationEngineConfig(auto_execute=True),
    )

    result = engine.run_once()

    assert result["processed"][0]["state"] == "static_call_failed"
    assert submitted == []
    assert records[0]["state"] == "static_call_failed"
    assert records[0]["error"] == "execution reverted"


def test_engine_skips_tick_when_auto_pause_active():
    records = []
    submitted = []
    engine = LiquidationEngine(
        _deps(records=records, submitted=submitted, controls={"auto_pause_active": True, "circuit_breaker_level": 2}),
        LiquidationEngineConfig(auto_execute=True),
    )

    result = engine.run_once()

    assert result["state"] == "paused"
    assert result["processed"] == []
    assert submitted == []
    assert records == []


def test_engine_price_event_prioritizes_affected_accounts():
    records = []
    submitted = []
    engine = LiquidationEngine(
        _deps(
            records=records,
            submitted=submitted,
            prices=[{"WAVAX": 20.0}, {"WAVAX": 19.6}],
            affected=["0xprice"],
        ),
        LiquidationEngineConfig(auto_execute=False, price_change_threshold_bps=100),
    )

    first = engine.run_once()
    second = engine.run_once()

    assert first["trigger"] == "poll"
    assert second["trigger"] == "price_event"
    assert second["changed_assets"] == ["WAVAX"]
    assert second["processed"][0]["account"] == "0xprice"


def test_engine_oracle_event_prioritizes_affected_accounts_without_waiting_for_poll_window():
    records = []
    submitted = []
    engine = LiquidationEngine(
        _deps(
            records=records,
            submitted=submitted,
            events=[[{"asset": "WETH", "price": 2000.0, "block_number": 123}]],
            affected=["0xoracle"],
        ),
        LiquidationEngineConfig(auto_execute=False, price_change_threshold_bps=100),
    )

    result = engine.run_once()

    assert result["trigger"] == "oracle_event"
    assert result["changed_assets"] == ["WETH"]
    assert result["oracle_events"][0]["block_number"] == 123
    assert result["processed"][0]["account"] == "0xoracle"


def test_engine_event_tick_keeps_full_poll_as_fallback_without_loading_all_accounts():
    records = []
    submitted = []
    full_loads = []
    deps = _deps(
        records=records,
        submitted=submitted,
        events=[[], []],
        affected=[],
    )
    deps = LiquidationEngineDependencies(
        **{
            **deps.__dict__,
            "load_accounts": lambda: full_loads.append("loaded") or ["0xfull"],
        }
    )
    engine = LiquidationEngine(
        deps,
        LiquidationEngineConfig(auto_execute=False, poll_interval_seconds=30, event_poll_interval_seconds=5),
    )

    event_tick = engine.run_once(allow_poll=False)
    fallback_poll = engine.run_once(allow_poll=True)

    assert event_tick["trigger"] == "idle"
    assert event_tick["processed"] == []
    assert full_loads == ["loaded"]
    assert fallback_poll["trigger"] == "poll"
    assert fallback_poll["processed"][0]["account"] == "0xfull"


def test_engine_default_event_poll_interval_is_under_five_seconds(monkeypatch):
    monkeypatch.delenv("LIQUIDATION_EVENT_POLL_SECONDS", raising=False)

    config = LiquidationEngineConfig.from_env()

    assert config.event_poll_interval_seconds <= 5.0


def test_engine_submission_error_is_redacted(monkeypatch):
    records = []
    submitted = []
    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "d" * 64
    monkeypatch.setenv("DATABASE_URL", database_url)

    deps = _deps(records=records, submitted=submitted)

    def fail_submit(payload):
        raise RuntimeError(f"submit failed: {database_url} private_key={private_key}")

    deps = LiquidationEngineDependencies(
        **{
            **deps.__dict__,
            "submit": fail_submit,
        }
    )
    engine = LiquidationEngine(deps, LiquidationEngineConfig(auto_execute=True))

    result = engine.run_once()

    error = result["processed"][0]["error"]
    recorded_error = records[-1]["error"]
    for value in (error, recorded_error):
        assert database_url not in value
        assert private_key not in value
        assert "secret-pass" not in value
        assert "abc123" not in value
        assert "[REDACTED]" in value
