import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading

import pytest

from core.db.storage_cow_execution import load_recent_cow_execution_attempts
from core.db.storage_cow_execution_jsonl import (
    append_cow_execution_attempts_jsonl,
    clear_cow_execution_attempts_jsonl,
    load_recent_cow_execution_attempts_jsonl,
)
from core.evidence_scope import evidence_scope_matches
from dex.runtime import cow_attempt_log
from web import control_panel_data_routes as shared


REAL_COW_LOG_ROOT = Path(__file__).resolve().parents[4] / "flashloan" / "src" / "runtime" / "logs" / "cow_execution_attempts"


def _attempt(*, network: str, chain_id: int, pair: str, observed_at: str) -> dict:
    return {
        "observed_at": observed_at,
        "network": network,
        "chain_id": chain_id,
        "pair": pair,
        "state": "quote_required",
        "execution_phase": "market_candidate",
        "checks_passed": False,
        "can_submit_order": False,
        "market_state": {
            "cow_filter": {
                "network": network,
                "chain_id": chain_id,
            }
        },
    }


def test_record_cow_attempt_list_safely_groups_attempts_by_scope(tmp_path, monkeypatch):
    database_path = tmp_path / "local_store.sqlite3"
    monkeypatch.setattr(cow_attempt_log, "database_url", lambda: str(database_path))
    monkeypatch.setattr(cow_attempt_log, "_cow_submission_pause_guard", lambda database_url=None: {"paused": False})

    attempts = [
        _attempt(
            network="robinhood",
            chain_id=4663,
            pair="robinhood-mainnet",
            observed_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        ),
        _attempt(
            network="robinhood-testnet",
            chain_id=46630,
            pair="robinhood-testnet",
            observed_at=datetime.now(timezone.utc).isoformat(),
        ),
    ]

    result = shared.record_cow_attempt_list_safely(attempts, database_url=None)

    assert result["recorded"] == 2
    assert result["source"] == "sqlite"
    assert len(result["ids"]) == 2
    assert result["log_paths"] == []
    rows = load_recent_cow_execution_attempts(database_path, limit=10)
    assert {row["network"] for row in rows} == {"robinhood", "robinhood-testnet"}


def test_record_cow_attempt_list_safely_can_disable_market_candidate_dedupe(tmp_path, monkeypatch):
    database_path = tmp_path / "local_store.sqlite3"
    monkeypatch.setattr(cow_attempt_log, "database_url", lambda: str(database_path))
    monkeypatch.setattr(cow_attempt_log, "_cow_submission_pause_guard", lambda database_url=None: {"paused": False})

    calls = []

    def fake_record(database_url, attempts, *, dedupe_market_candidates):
        calls.append(
            {
                "count": len(attempts),
                "dedupe_market_candidates": dedupe_market_candidates,
            }
        )
        return [1]

    monkeypatch.setattr(cow_attempt_log, "record_cow_execution_attempts", fake_record)

    result = shared.record_cow_attempt_list_safely(
        [
            _attempt(
                network="robinhood-testnet",
                chain_id=46630,
                pair="dedupe-disabled",
                observed_at="2026-08-29T09:43:53+00:00",
            )
        ],
        database_url=None,
        dedupe_market_candidates=False,
    )

    assert result["recorded"] == 1
    assert calls[0]["dedupe_market_candidates"] is False
    assert result["source"] == "sqlite"


def test_jsonl_directory_root_aggregates_and_clears_per_network(tmp_path):
    root = tmp_path / "cow_execution_attempts"
    legacy_path = root.with_suffix(".jsonl")
    mainnet_path = root / "robinhood" / "4663" / "attempts.jsonl"
    testnet_path = root / "robinhood-testnet" / "46630" / "attempts.jsonl"

    assert append_cow_execution_attempts_jsonl(
        legacy_path,
        [
            _attempt(
                network="robinhood",
                chain_id=4663,
                pair="legacy-mainnet",
                observed_at="2026-08-29T09:43:50+00:00",
            )
        ],
    ) == 1
    assert append_cow_execution_attempts_jsonl(
        mainnet_path,
        [
            _attempt(
                network="robinhood",
                chain_id=4663,
                pair="nested-mainnet",
                observed_at="2026-08-29T09:43:51+00:00",
            )
        ],
    ) == 1
    assert append_cow_execution_attempts_jsonl(
        testnet_path,
        [
            _attempt(
                network="robinhood-testnet",
                chain_id=46630,
                pair="nested-testnet",
                observed_at="2026-08-29T09:43:52+00:00",
            )
        ],
    ) == 1

    rows = load_recent_cow_execution_attempts_jsonl(root, limit=10)
    filtered = load_recent_cow_execution_attempts_jsonl(root, limit=10, networks=["robinhood-testnet"], chain_ids=[46630])

    assert len(rows) == 3
    assert {row["network"] for row in rows} == {"robinhood", "robinhood-testnet"}
    assert len(filtered) == 1
    assert filtered[0]["pair"] == "nested-testnet"

    cleared = clear_cow_execution_attempts_jsonl(root, networks=["robinhood-testnet"])
    remaining = load_recent_cow_execution_attempts_jsonl(root, limit=10)

    assert cleared["cleared"] == 1
    assert cleared["remaining"] == 2
    assert {row["pair"] for row in remaining} == {"legacy-mainnet", "nested-mainnet"}


def test_jsonl_concurrent_appends_do_not_lose_rows_or_leave_tmp_files(tmp_path):
    path = tmp_path / "attempts.jsonl"
    failures = []

    def append_batch(worker: int) -> None:
        try:
            append_cow_execution_attempts_jsonl(
                path,
                [
                    _attempt(
                        network="robinhood-testnet",
                        chain_id=46630,
                        pair=f"worker-{worker}-{index}",
                        observed_at=f"2026-10-20T00:{worker:02d}:{index:02d}+00:00",
                    )
                    for index in range(3)
                ],
                dedupe_market_candidates=False,
            )
        except Exception as exc:  # pragma: no cover - surfaced by assertions
            failures.append(exc)

    threads = [threading.Thread(target=append_batch, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert failures == []
    rows = load_recent_cow_execution_attempts_jsonl(path, limit=100)
    assert len(rows) == 24
    assert {row["pair"] for row in rows} == {
        f"worker-{worker}-{index}"
        for worker in range(8)
        for index in range(3)
    }
    assert list(tmp_path.glob("*.tmp")) == []


def test_real_cow_execution_jsonl_rows_keep_consistent_scope():
    path = REAL_COW_LOG_ROOT / "robinhood-testnet" / "46630" / "attempts.jsonl"
    if not path.exists():
        pytest.skip("real robinhood-testnet cow execution log is not present")

    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # Historical runtime log rows can be truncated after an
            # interrupted append; scope checks only need complete rows.
            continue

    if not rows:
        pytest.skip("real robinhood-testnet cow execution log is empty")
    for row in rows:
        assert evidence_scope_matches(
            row,
            networks=["robinhood-testnet"],
            chain_ids=[46630],
        )
