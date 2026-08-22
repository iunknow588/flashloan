from liquidation.refresh_service import LiquidationRefreshRuntime, start_liquidation_refresh_loop


def test_refresh_service_runs_incremental_scan_without_forcing_backfill(monkeypatch):
    calls = []
    waits = []
    holder = {"thread": None}
    discovery_cache = {"last_backfill_monotonic": 999.0}

    class FakeStop:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            waits.append(seconds)
            self.stopped = True

    class FakeThread:
        def __init__(self, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def is_alive(self):
            return False

        def start(self):
            self.target()

    stop_event = FakeStop()
    runtime = LiquidationRefreshRuntime(
        get_thread=lambda: holder["thread"],
        set_thread=lambda thread: holder.update({"thread": thread}),
        thread_factory=FakeThread,
        stop_event=stop_event,
        enabled=lambda: True,
        discover_accounts=lambda force_full=False: calls.append(
            ("discover", force_full)
        ) or {},
        scan_borrow_pool=lambda force=False: calls.append(("scan", force)) or {},
        discovery_cache=discovery_cache,
        backfill_interval_seconds=lambda: 999999.0,
        cooldown_seconds=lambda: 1.0,
    )

    monkeypatch.setattr("liquidation.refresh_service.time.monotonic", lambda: 1000.0)

    start_liquidation_refresh_loop(runtime)

    assert calls == [("discover", False), ("scan", False)]
    assert waits == [1.0]
    assert holder["thread"].name == "liquidation-refresh"
