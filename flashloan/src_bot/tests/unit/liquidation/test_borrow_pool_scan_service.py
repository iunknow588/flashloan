from types import SimpleNamespace

from liquidation.borrow_pool_scan_service import (
    BorrowPoolScanRuntime,
    run_borrow_pool_scan_payload,
)


class BusyLock:
    @staticmethod
    def acquire(blocking=False):
        return False


def _runtime(**overrides):
    cache = {
        "running": False,
        "stage": "idle",
        "started_at": None,
        "finished_at": None,
    }
    values = {
        "scan_cache": cache,
        "scan_lock": BusyLock(),
        "database_url": "postgresql://example",
        "config": SimpleNamespace(watch_health_factor=1.5),
        "scope": {"market_id": "avalanche-aave-v3", "chain_id": 43114},
        "market_kwargs": {},
        "page_size": 20,
        "risk_page": 1,
        "high_page": 1,
        "core_page": 1,
        "ttl_seconds": 300.0,
        "force": False,
        "load_display": lambda **kwargs: {"rows": [], "summary": {"configured": True}},
        "blocked_payload": lambda **kwargs: {"summary": kwargs},
        "run_scan": lambda *args, **kwargs: None,
        "load_borrow_health": lambda *args, **kwargs: [],
        "build_tiers": lambda *args, **kwargs: {},
        "record_batch": lambda *args, **kwargs: {},
        "load_scan_configs": lambda *args, **kwargs: [],
        "build_scan_summary": lambda **kwargs: kwargs,
        "build_pool_summary": lambda *args, **kwargs: {},
        "decision_from_payload": lambda payload: {},
        "account_tiers": lambda: {},
        "database_fingerprint": lambda database_url: "db:example",
        "error_message": lambda error: str(error),
    }
    values.update(overrides)
    return BorrowPoolScanRuntime(**values)


def test_scan_service_returns_matching_cached_payload_with_suppression(monkeypatch):
    cache = {
        "running": False,
        "stage": "idle",
        "started_at": "2026-08-21T00:00:00+00:00",
        "finished_at": None,
        "borrow_pool_updated_at": 100.0,
        "borrow_pool_payload": {
            "rows": [{"account": "0x1"}],
            "summary": {
                "database_url_fingerprint": "db:example",
                "market_id": "avalanche-aave-v3",
                "chain_id": 43114,
            },
        },
    }
    monkeypatch.setattr("liquidation.borrow_pool_scan_service.time.monotonic", lambda: 120.0)

    payload = run_borrow_pool_scan_payload(_runtime(scan_cache=cache))

    assert payload["rows"] == [{"account": "0x1"}]
    assert payload["summary"]["scan_suppressed"] is True
    assert payload["summary"]["scan_response_source"] == "cached_scan_suppressed"
    assert payload["summary"]["scan_cooldown_remaining_seconds"] == 280.0


def test_scan_service_uses_display_payload_for_nondefault_pagination():
    calls = []

    def load_display(**kwargs):
        calls.append(kwargs)
        return {"rows": [], "summary": {"scan_response_source": "database_display"}}

    payload = run_borrow_pool_scan_payload(
        _runtime(risk_page=2, high_page=3, core_page=4, load_display=load_display)
    )

    assert payload["summary"]["scan_response_source"] == "database_display"
    assert calls == [{
        "page_size": 20,
        "risk_page": 2,
        "high_page": 3,
        "core_page": 4,
        "skip_schema": True,
    }]


def test_scan_service_marks_display_response_running_when_lock_is_busy():
    payload = run_borrow_pool_scan_payload(_runtime())

    assert payload["summary"]["scan_running"] is True
    assert payload["summary"]["stage"] == "idle"
