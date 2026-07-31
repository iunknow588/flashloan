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
    affected=None,
    static_passed=True,
):
    price_iter = iter(prices or [])

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
            "blocked_reasons": [] if static_passed else ["static_call_failed"],
        }

    def submit(payload):
        submitted.append(payload["account"])
        return {**payload, "tx_hash": "0xabc", "receipt": {"status": 1}}

    def record_attempt(**kwargs):
        records.append(kwargs)

    def load_prices():
        return next(price_iter)

    return LiquidationEngineDependencies(
        load_accounts=lambda: ["0x1"],
        build_payload=build_payload,
        simulate_static_call=simulate,
        submit=submit,
        record_attempt=record_attempt,
        load_controls=lambda: controls or {"auto_pause_active": False, "circuit_breaker_level": 0},
        load_price_snapshot=load_prices if prices is not None else None,
        load_affected_accounts=(lambda assets: affected or []) if affected is not None else None,
    )


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
