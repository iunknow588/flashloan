from pathlib import Path

from core.db.storage_cow_execution import append_cow_execution_attempts_jsonl, clear_cow_execution_attempts_jsonl, load_recent_cow_execution_attempts_jsonl
from dex.runtime import cow_attempt_log
from web import control_panel_data_routes as shared


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


def test_cow_execution_attempt_log_path_uses_chain_scoped_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(cow_attempt_log, "COW_EXECUTION_ATTEMPT_LOG_ROOT", tmp_path / "cow_execution_attempts")

    path = shared.cow_execution_attempt_log_path(network="robinhood-testnet", chain_id=46630)
    fallback_path = shared.cow_execution_attempt_log_path(
        market_state={"cow_filter": {"network": "robinhood-testnet", "chain_id": 46630}}
    )

    assert path.as_posix().endswith("cow_execution_attempts/robinhood-testnet/46630/attempts.jsonl")
    assert fallback_path == path


def test_record_cow_attempt_list_safely_groups_attempts_by_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(cow_attempt_log, "COW_EXECUTION_ATTEMPT_LOG_ROOT", tmp_path / "cow_execution_attempts")
    monkeypatch.setattr(cow_attempt_log, "_cow_submission_pause_guard", lambda database_url=None: {"paused": False})

    attempts = [
        _attempt(
            network="robinhood",
            chain_id=4663,
            pair="robinhood-mainnet",
            observed_at="2026-08-29T09:43:51+00:00",
        ),
        _attempt(
            network="robinhood-testnet",
            chain_id=46630,
            pair="robinhood-testnet",
            observed_at="2026-08-29T09:43:52+00:00",
        ),
    ]

    result = shared.record_cow_attempt_list_safely(attempts, database_url=None)

    assert result["recorded"] == 2
    assert len(result["log_paths"]) == 2
    normalized_paths = [Path(path).as_posix() for path in result["log_paths"]]
    assert any(path.endswith("cow_execution_attempts/robinhood/4663/attempts.jsonl") for path in normalized_paths)
    assert any(path.endswith("cow_execution_attempts/robinhood-testnet/46630/attempts.jsonl") for path in normalized_paths)
    assert (tmp_path / "cow_execution_attempts" / "robinhood" / "4663" / "attempts.jsonl").exists()
    assert (tmp_path / "cow_execution_attempts" / "robinhood-testnet" / "46630" / "attempts.jsonl").exists()


def test_record_cow_attempt_list_safely_can_disable_market_candidate_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(cow_attempt_log, "COW_EXECUTION_ATTEMPT_LOG_ROOT", tmp_path / "cow_execution_attempts")
    monkeypatch.setattr(cow_attempt_log, "_cow_submission_pause_guard", lambda database_url=None: {"paused": False})

    calls = []

    def fake_append(path, attempts, *, retention_days, dedupe_market_candidates):
        calls.append(
            {
                "path": Path(path).as_posix(),
                "count": len(attempts),
                "dedupe_market_candidates": dedupe_market_candidates,
                "retention_days": retention_days,
            }
        )
        return len(attempts)

    monkeypatch.setattr(cow_attempt_log, "append_cow_execution_attempts_jsonl", fake_append)

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
    assert calls[0]["path"].endswith("cow_execution_attempts/robinhood-testnet/46630/attempts.jsonl")


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
