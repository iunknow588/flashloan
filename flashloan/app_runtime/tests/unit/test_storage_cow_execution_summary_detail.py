from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.db.storage_common import db_connection

from core.db.storage_cow_execution import (
    backfill_cow_execution_attempt_summaries,
    count_cow_execution_attempts_sqlite_grouped,
    load_auto_execute_log_stats,
    load_cow_execution_attempt_detail,
    load_recent_cow_execution_attempt_validation_rows,
    load_recent_cow_execution_attempts_sqlite_grouped,
    record_cow_execution_attempts,
)


def _attempt(pair: str, state: str = "quote_required", idx: int = 1) -> dict:
    observed_at = (
        datetime.now(timezone.utc) - timedelta(minutes=idx)
    ).replace(microsecond=0).isoformat()
    return {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "market_id": "dex-arbitrage-46630-uniswap-v4",
        "pair": pair,
        "state": state,
        "execution_phase": "market_candidate",
        "checks_passed": False,
        "can_submit_order": False,
        "observed_at": observed_at,
        "quote": {
            "live_onchain_evidence": {
                "status": "not_live_verified",
                "executionTrust": "static_reference_only",
                "blockers": ["business blocker"],
            },
            "quote_analysis": {
                "probe_records": [{"borrowAmount": "1", "netProfit": "2"}],
                "best_amount_calculation": {
                    "probe_records": [{"borrowAmount": "1", "netProfit": "2"}],
                },
            },
        },
    }


def test_record_writes_summary_json_and_grouped_returns_compact(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    record_cow_execution_attempts(path, [_attempt("TZ -> TB -> TA -> TZ", idx=1)])

    grouped = load_recent_cow_execution_attempts_sqlite_grouped(
        path,
        limit=10,
        networks=["robinhood-testnet"],
        chain_ids=[46630],
    )
    rows = grouped.get("not_executable") or []
    assert len(rows) == 1
    row = rows[0]
    assert row["review_category"] == "not_executable"
    assert row["review_summary"]["onchain_evidence"]["blockers"] == ["business blocker"]
    assert "quote_analysis" in row["quote"]
    assert "candidate_pools" not in row["quote"]
    assert "quoteAnalysis" not in row
    assert "quote_analysis" not in row


def test_counts_and_detail_read_sqlite(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    ids = record_cow_execution_attempts(
        path,
        [
            _attempt("TZ -> TB -> TA -> TZ", idx=1),
            _attempt("ETH -> USDC -> ETH", idx=2),
        ],
    )
    counts = count_cow_execution_attempts_sqlite_grouped(
        path,
        networks=["robinhood-testnet"],
        chain_ids=[46630],
    )
    assert counts["not_executable"] == 2
    assert counts["total"] == 2

    detail = load_cow_execution_attempt_detail(path, ids[0])
    assert detail is not None
    assert detail["id"] == ids[0]
    assert detail["quote"]["quote_analysis"]["probe_records"]


def test_backfill_summary_populates_missing_rows(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    record_cow_execution_attempts(path, [_attempt("TZ -> TA -> TB -> TZ", idx=3)])
    with db_connection(path) as connection:
        connection.execute("UPDATE cow_execution_reviews SET summary_json = ''")

    report = backfill_cow_execution_attempt_summaries(path)
    assert report["updated"] == 1
    rows = load_recent_cow_execution_attempts_sqlite_grouped(
        path,
        limit=10,
        networks=["robinhood-testnet"],
        chain_ids=[46630],
    )
    assert len(rows.get("not_executable") or []) == 1


def test_validation_rows_extract_summary_json_fields(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    record_cow_execution_attempts(
        path,
        [_attempt("TZ -> TB -> TA -> TZ", idx=4)],
    )

    rows = load_recent_cow_execution_attempt_validation_rows(path, limit=10)

    assert len(rows) == 1
    assert rows[0]["id"] == 1
    assert rows[0]["state"] == "quote_required"
    assert rows[0]["observed_at"].endswith("+00:00")
    assert rows[0]["review_category"] == "not_executable"
    assert "quote" not in rows[0]


def test_load_auto_execute_log_stats_reads_only_success_summaries(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    executed = _attempt("TZ -> TB -> TA -> TZ", idx=1)
    executed["tx_hash"] = "0xabcdef"
    executed["auto_execute"] = {
        "executed": True,
        "observed_at": executed["observed_at"],
        "result": {"txHash": "0xabcdef"},
    }
    skipped = _attempt("ETH -> USDC -> ETH", idx=2)
    skipped["auto_execute"] = {"executed": False, "result": {}}
    assert record_cow_execution_attempts(path, [executed, skipped])

    stats = load_auto_execute_log_stats(
        path,
        limit=10,
        networks=["robinhood-testnet"],
        chain_ids=[46630],
    )

    assert stats == {
        "auto_execute_count": 1,
        "last_auto_execute_at": executed["observed_at"],
    }


def test_load_auto_execute_log_stats_falls_back_for_legacy_summary(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    executed = _attempt("TZ -> TB -> TA -> TZ", idx=1)
    executed["tx_hash"] = "0xabcdef"
    executed["auto_execute"] = {
        "executed": True,
        "observed_at": executed["observed_at"],
        "result": {"txHash": "0xabcdef"},
    }
    record_cow_execution_attempts(path, [executed])
    with db_connection(path) as connection:
        connection.execute("UPDATE cow_execution_attempts SET summary_json = ''")

    assert (
        load_auto_execute_log_stats(
            path,
            limit=10,
            networks=["robinhood-testnet"],
            chain_ids=[46630],
        )
        is None
    )
