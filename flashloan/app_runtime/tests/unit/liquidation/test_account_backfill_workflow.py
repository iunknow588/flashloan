from datetime import datetime, timezone
from threading import Event, Lock

from bot.liquidation.account_backfill_workflow import (
    AccountBackfillRuntime,
    run_account_backfill_once,
)


def _window():
    return (
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 2, tzinfo=timezone.utc),
        100,
        200,
        100,
        {"discovery_scan_progress": {}},
        "historical-backfill",
    )


def _runtime(**overrides):
    cache = {
        "running": False,
        "stop_requested": False,
        "started_at": None,
        "finished_at": None,
        "stage": "idle",
        "last_result": None,
        "progress": {},
        "error": None,
    }
    values = {
        "cache": cache,
        "lock": Lock(),
        "stop_event": Event(),
        "status_payload": lambda: dict(cache),
        "database_url": lambda: "local_store.sqlite3",
        "auto_discovery_enabled": lambda: True,
        "pool_address": lambda: "0xpool",
        "discovery_window": lambda force_full=True: _window(),
        "account_registry_window": lambda: {"active_count": 1},
        "chunk_size": lambda: 1000,
        "rpc_urls": lambda: ["https://rpc.example"],
        "resolve_block_range": lambda rpc_url, from_block, to_block: (123, 100, 200),
        "discover_borrowers": lambda *args, **kwargs: ["0x1"],
        "configured_database_url": lambda: "local_store.sqlite3",
        "upsert_accounts": lambda *args, **kwargs: None,
        "invalidate_account_cache": lambda: None,
        "set_progress": lambda progress: cache.update({"progress": progress}),
        "error_message": lambda error: str(error),
    }
    values.update(overrides)
    return AccountBackfillRuntime(**values)


def test_backfill_workflow_rejects_missing_database_before_locking():
    runtime = _runtime(database_url=lambda: None)

    result = run_account_backfill_once(runtime)

    assert result == {
        "started": False,
        "saved": False,
        "count": 0,
        "error": "local KV store unavailable",
    }


def test_backfill_workflow_saves_discovered_accounts_and_invalidates_cache():
    captured = {}
    runtime = _runtime(
        upsert_accounts=lambda database_url, accounts, **kwargs: captured.update(
            {"database_url": database_url, "accounts": accounts, "kwargs": kwargs}
        ),
        invalidate_account_cache=lambda: captured.update({"invalidated": True}),
    )

    result = run_account_backfill_once(runtime)

    assert result["saved"] is True
    assert result["status"] == "success"
    assert result["count"] == 1
    assert captured["database_url"] == "local_store.sqlite3"
    assert captured["accounts"] == ["0x1"]
    assert captured["kwargs"]["source"] == "one-year-gap-fill"
    assert captured["invalidated"] is True
    assert runtime.cache["running"] is False
    assert runtime.cache["stage"] == "idle"


def test_backfill_workflow_reports_stopped_after_scan_callback_sets_stop():
    runtime = _runtime()

    def discover(*args, **kwargs):
        kwargs["stop_event"].set()
        return ["0x1"]

    runtime = _runtime(discover_borrowers=discover)

    result = run_account_backfill_once(runtime)

    assert result["saved"] is True
    assert result["status"] == "stopped"
    assert result["stopped"] is True
    assert runtime.cache["stop_requested"] is True
