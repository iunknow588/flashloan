import json

from core.db.storage_cow_execution import (
    load_latest_cow_execution_rows_by_pair,
    record_cow_execution_attempts,
)


def _review_row(
    *,
    pair,
    created_at,
    id_hint=0,
    status="route_unprofitable",
    category="not_executable",
):
    return {
        "id": id_hint,
        "network": "robinhood",
        "chain_id": 4663,
        "market_id": "dex-arbitrage-4663-uniswap_v4",
        "pair": pair,
        "route_path": [part.strip() for part in pair.split("->")],
        "category": category,
        "created_at": created_at,
        "state": "ready_for_transaction_development",
        "quote": {
            "live_onchain_evidence": {
                "status": status,
                "blockers": [] if status == "live_verified" else ["no positive profit"],
            }
        },
        "auto_execute": {},
    }


def _execution_row(
    *,
    pair,
    created_at,
    category,
    tx_hash,
):
    return {
        "id": 0,
        "network": "robinhood",
        "chain_id": 4663,
        "market_id": "dex-arbitrage-4663-uniswap_v4",
        "pair": pair,
        "category": category,
        "review_category": category,
        "created_at": created_at,
        "state": "execution_success" if category == "execution_success" else "execution_failed",
        "tx_hash": tx_hash,
        "transaction_hash": tx_hash,
        "quote": {},
        "auto_execute": {},
    }


def test_load_latest_rows_keeps_one_row_per_pair(tmp_path):
    database = str(tmp_path / "cow.db")
    record_cow_execution_attempts(
        database,
        [
            _review_row(pair="TZ -> TA -> TB -> TZ", created_at="2026-10-30T00:00:00+00:00", id_hint=1),
            _review_row(pair="TZ -> TA -> TB -> TZ", created_at="2026-10-30T00:02:00+00:00", id_hint=2),
            _review_row(pair="TZ -> TB -> TA -> TZ", created_at="2026-10-30T00:03:00+00:00", id_hint=3),
        ],
    )

    latest = load_latest_cow_execution_rows_by_pair(
        database,
        networks=["robinhood"],
        chain_ids=[4663],
        categories=["not_executable"],
    )

    # Two directions of the same closed loop collapse into one loop_key when
    # merged by the route layer, so the storage helper only needs one row per
    # direction pair: 2 records for the loop.
    assert len(latest) == 2
    by_pair = {row["pair"]: row for row in latest}
    assert by_pair["TZ -> TA -> TB -> TZ"]["id"] == 2
    assert by_pair["TZ -> TB -> TA -> TZ"]["id"] == 3


def test_load_latest_rows_includes_execution_attempts(tmp_path):
    database = str(tmp_path / "cow.db")
    record_cow_execution_attempts(
        database,
        [
            _review_row(pair="TZ -> TA -> TB -> TZ", created_at="2026-10-30T00:00:00+00:00", id_hint=1),
            _execution_row(
                pair="TZ -> TA -> TB -> TZ",
                created_at="2026-10-30T00:01:00+00:00",
                category="execution_success",
                tx_hash="0x" + "ab" * 32,
            ),
            _execution_row(
                pair="TZ -> TB -> TA -> TZ",
                created_at="2026-10-30T00:02:00+00:00",
                category="execution_failed",
                tx_hash="0x" + "cd" * 32,
            ),
        ],
    )

    latest_review = load_latest_cow_execution_rows_by_pair(
        database,
        networks=["robinhood"],
        chain_ids=[4663],
        categories=["not_executable"],
    )
    latest_execution = load_latest_cow_execution_rows_by_pair(
        database,
        networks=["robinhood"],
        chain_ids=[4663],
        categories=["execution_success", "execution_failed"],
    )

    assert len(latest_review) == 1
    assert len(latest_execution) == 2
    categories = {row["review_category"] for row in latest_execution}
    assert categories == {"execution_success", "execution_failed"}


def test_summary_json_fallback_uses_payload_when_empty(tmp_path):
    database = str(tmp_path / "cow.db")
    record_cow_execution_attempts(
        database,
        [_review_row(pair="TZ -> TA -> TB -> TZ", created_at="2026-10-30T00:00:00+00:00", id_hint=1)],
    )

    # Simulate a legacy row whose summary_json was empty; the loader should
    # fall back to payload_json and still expose pair/created_at.
    from core.db.storage_common import db_connection

    with db_connection(database) as connection:
        connection.execute("UPDATE cow_execution_reviews SET summary_json = ''")

    latest = load_latest_cow_execution_rows_by_pair(
        database,
        networks=["robinhood"],
        chain_ids=[4663],
        categories=["not_executable"],
    )

    assert len(latest) == 1
    assert latest[0]["pair"] == "TZ -> TA -> TB -> TZ"
    assert latest[0]["chain_id"] == 4663
