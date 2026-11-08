from pathlib import Path

import pytest

from market.tools import validate_live_assumptions as module


def test_sqlite_source_uses_summary_loader(tmp_path, monkeypatch):
    calls = []

    def fake_validation_rows(source, limit=5000):
        calls.append((str(source), limit))
        return [{"id": 1, "state": "quote_required", "observed_at": "2026-10-23T00:00:00Z"}]

    monkeypatch.setattr(module, "load_recent_cow_execution_attempt_validation_rows", fake_validation_rows)

    rows = module._load_cow_execution_rows(Path("local.sqlite3"), limit=7)

    assert rows == [{"id": 1, "state": "quote_required", "observed_at": "2026-10-23T00:00:00Z"}]
    assert calls == [("local.sqlite3", 7)]


def test_jsonl_source_is_rejected(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        module,
        "load_recent_cow_execution_attempt_validation_rows",
        lambda *args, **kwargs: calls.append(args),
    )
    with pytest.raises(ValueError, match="SQLite store"):
        module._load_cow_execution_rows(tmp_path / "attempts.jsonl", limit=3)
    assert calls == []


def test_sqlite_source_reads_full_payload_for_settled_rows(tmp_path, monkeypatch):
    def fake_validation_rows(source, limit=5000):
        return [
            {"id": 1, "state": "quote_required", "observed_at": "2026-10-23T00:00:00Z"},
            {"id": 2, "state": "execution_success", "observed_at": "2026-10-23T00:01:00Z"},
        ]

    def fake_detail(source, attempt_id):
        assert attempt_id == 2
        return {
            "id": 2,
            "state": "execution_success",
            "quote": {"quote_call": {"finishedAt": "2026-10-23T00:01:00Z"}},
            "settled_at": "2026-10-23T00:01:04Z",
        }

    monkeypatch.setattr(module, "load_recent_cow_execution_attempt_validation_rows", fake_validation_rows)
    monkeypatch.setattr(module, "load_cow_execution_attempt_detail", fake_detail)

    rows = module._load_cow_execution_rows(Path("local.sqlite3"), limit=10)

    assert rows[0] == {"id": 1, "state": "quote_required", "observed_at": "2026-10-23T00:00:00Z"}
    assert rows[1] == {
        "id": 2,
        "state": "execution_success",
        "quote": {"quote_call": {"finishedAt": "2026-10-23T00:01:00Z"}},
        "settled_at": "2026-10-23T00:01:04Z",
    }


def test_resolve_input_paths_makes_relative_cow_absolute():
    liquidation, cow, scan = module._resolve_input_paths(
        Path("runtime/logs/liquidation_events.jsonl"),
        Path("flashloan/src/runtime/local_store.sqlite3"),
        Path("runtime/logs/liquidation_health_scans.jsonl"),
    )

    assert cow.is_absolute()
    assert cow.name == "local_store.sqlite3"
    assert liquidation.is_absolute()
    assert scan.is_absolute()
