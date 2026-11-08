from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

from core.db.storage_common import db_connection
from core.db.storage_cow_execution import (
    COW_EXECUTION_REVIEW_TABLE,
    COW_EXECUTION_SUBMISSION_TABLE,
    load_recent_cow_execution_attempts_sqlite_grouped,
    record_cow_execution_attempts,
)
from core.db.storage_cow_execution_compact import compact_cow_execution_attempt_row


def _attempt(
    *,
    state: str,
    phase: str,
    checks_passed: bool,
    can_submit_order: bool,
    tx_hash: str = "",
    selected: dict | None = None,
) -> dict:
    row: dict = {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "market_id": "dex-arbitrage-46630-uniswap-v4",
        "pair": "ETH -> USDC -> ETH",
        "state": state,
        "execution_phase": phase,
        "checks_passed": checks_passed,
        "can_submit_order": can_submit_order,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    if tx_hash:
        row["tx_hash"] = tx_hash
    if selected:
        row.update(selected)
    return row


def _query_row(database_url: str, table: str, row_id: int) -> dict:
    with db_connection(database_url) as connection:
        return connection.execute(
            f"""
            SELECT selected_profit_raw, selected_profit_amount,
                   selected_profit_amount_symbol, selected_profit_source,
                   selected_profit_missing_reason
            FROM {table}
            WHERE id = ?
            """,
            (int(row_id),),
        ).fetchone()


def test_selected_profit_columns_persist_on_review_and_execution_rows(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    selected = {
        "selectedProfitRaw": "0x0493e0",
        "selectedProfitAmount": 0.5,
        "selectedProfitAmountSymbol": "ETH",
        "selectedProfitSource": "native_rust_contract_preview",
    }
    review = _attempt(
        state="quote_required",
        phase="market_candidate",
        checks_passed=False,
        can_submit_order=False,
        selected=selected,
    )
    success = _attempt(
        state="execution_success",
        phase="order_submission",
        checks_passed=True,
        can_submit_order=True,
        tx_hash="0x" + "1" * 64,
        selected=selected,
    )

    ids = record_cow_execution_attempts(path, [review, success])

    assert len(ids) == 2
    review_row = _query_row(path, COW_EXECUTION_REVIEW_TABLE, ids[0])
    success_row = _query_row(path, COW_EXECUTION_SUBMISSION_TABLE, ids[0])
    assert review_row["selected_profit_raw"] == "0x0493e0"
    assert success_row["selected_profit_amount"] == 0.5
    assert review_row["selected_profit_amount_symbol"] == "ETH"
    assert success_row["selected_profit_source"] == "native_rust_contract_preview"


def test_missing_signed_value_stays_null_with_concrete_pnl_unavailable_reason(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    review = _attempt(
        state="quote_required",
        phase="market_candidate",
        checks_passed=False,
        can_submit_order=False,
        selected={
            "selectedProfitSource": "concrete_pnl_unavailable",
            "selectedProfitMissingReason": (
                "concrete P&L could not be resolved; 0.0 is not an "
                "acceptable review result"
            ),
        },
    )

    ids = record_cow_execution_attempts(path, [review])
    row = _query_row(path, COW_EXECUTION_REVIEW_TABLE, ids[0])

    assert row["selected_profit_raw"] is None
    assert row["selected_profit_amount"] is None
    assert row["selected_profit_source"] == "concrete_pnl_unavailable"
    assert "concrete P&L could not be resolved" in row["selected_profit_missing_reason"]


def test_compact_summary_carries_selected_profit_view(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    selected = {
        "selectedProfitRaw": "0x2",
        "selectedProfitAmount": 2.0,
        "selectedProfitAmountSymbol": "ETH",
        "selectedProfitSource": "native_rust_contract_preview",
        "referenceNetProfitAmount": 1.5,
    }
    row = _attempt(
        state="quote_required",
        phase="market_candidate",
        checks_passed=False,
        can_submit_order=False,
        selected=selected,
    )
    row["quote"] = {
        "quote_analysis": {
            "referenceNetProfitAmount": 1.5,
            "selectedProfitAmount": 2.0,
            "selectedProfitAmountSymbol": "ETH",
            "selectedProfitSource": "native_rust_contract_preview",
        }
    }

    compact = compact_cow_execution_attempt_row(row)

    assert compact["selectedProfitRaw"] == "0x2"
    assert compact["selectedProfitAmount"] == 2.0
    assert compact["selectedProfitSource"] == "native_rust_contract_preview"


def test_compact_summary_enriches_generic_missing_reason_with_concrete_blockers():
    row = _attempt(
        state="quote_required",
        phase="market_candidate",
        checks_passed=False,
        can_submit_order=False,
    )
    row["selectedProfitRaw"] = "0x0"
    row["selectedProfitSource"] = "native_rust_contract_preview"
    row["selectedProfitMissingReason"] = (
        "native result has no signed borrow-token profit value; "
        "zero placeholder does not prove a zero or negative loss"
    )
    row["blocked_reasons"] = [
        "chain review failed: previewBestPoolDetailed unavailable",
        "contract detailed preview concrete value: netProductDelta=-0.066; "
        "netProduct below 1e18 confirms the route is not profitable",
    ]
    row["quote"] = {
        "live_onchain_evidence": {
            "blockers": [
                "chain review failed: NoProfitableRoute",
            ],
            "conclusion": "chain review blocked; route not profitable",
        }
    }

    compact = compact_cow_execution_attempt_row(row)

    assert compact["selectedProfitAmount"] is None
    assert (
        compact["selectedProfitSource"]
        == "concrete_pnl_unavailable"
    )
    assert "netProductDelta=-0.066" in compact["selectedProfitMissingReason"]
    assert "native result has no signed borrow-token profit value" not in (
        compact["selectedProfitMissingReason"]
    )
    assert "NoProfitableRoute" in compact["selectedProfitMissingReason"]


def test_compact_summary_keeps_missing_concrete_pnl_for_failure_payload():
    row = _attempt(
        state="review_failed",
        phase="review",
        checks_passed=False,
        can_submit_order=False,
    )
    row["errorKey"] = "native_task_config_unavailable"
    row["failureReason"] = "missing_pool_candidates_or_token_metadata"
    row["reviewFailureKind"] = "native_task_config_unavailable"
    row["reviewFailureReasons"] = ["missing_pool_candidates_or_token_metadata"]
    row["error"] = (
        '[{"routeSymbols": ["USDE", "USDG", "ETH", "USDE"], '
        '"reason": "missing_pool_candidates_or_token_metadata"}]'
    )

    compact = compact_cow_execution_attempt_row(row)

    assert compact["selectedProfitAmount"] is None
    assert (
        compact["selectedProfitSource"]
        == "concrete_pnl_unavailable"
    )
    assert "native_task_config_unavailable" in compact["selectedProfitMissingReason"]
    assert "missing_pool_candidates_or_token_metadata" in compact["selectedProfitMissingReason"]
    assert '"routeSymbols"' not in compact["selectedProfitMissingReason"]


def test_compact_summary_keeps_blockers_when_amount_is_present_but_reason_empty():
    row = _attempt(
        state="quote_required",
        phase="market_candidate",
        checks_passed=False,
        can_submit_order=False,
    )
    row["selectedProfitRaw"] = "-0xde0b6b3a7640000"
    row["selectedProfitAmount"] = -1.0
    row["selectedProfitAmountSymbol"] = "SPY"
    row["blocked_reasons"] = [
        "chain review failed: NoProfitableRoute()",
        "contract detailed preview concrete value: netProductDelta=-1; "
        "netProduct below 1e18 confirms the route is not profitable",
    ]

    compact = compact_cow_execution_attempt_row(row)

    assert compact["selectedProfitAmount"] == -1.0
    assert "NoProfitableRoute" in compact["selectedProfitMissingReason"]
    assert "netProductDelta=-1" in compact["selectedProfitMissingReason"]


def test_summary_rows_from_sql_enriches_stale_summary_reason_from_payload(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    row = _attempt(
        state="quote_required",
        phase="market_candidate",
        checks_passed=False,
        can_submit_order=False,
    )
    row["selectedProfitAmount"] = -1.0
    row["selectedProfitRaw"] = "-0xde0b6b3a7640000"
    row["blocked_reasons"] = [
        "chain review failed: NoProfitableRoute()",
        "contract detailed preview concrete value: netProductDelta=-1",
    ]
    row_id = record_cow_execution_attempts(path, [row])[0]

    with db_connection(path) as connection:
        connection.execute(
            "UPDATE cow_execution_reviews SET summary_json = ? WHERE id = ?",
            (
                json.dumps({"id": row_id, "selectedProfitAmount": -1.0}),
                row_id,
            ),
        )

    from core.db.storage_cow_execution import _summary_rows_from_sql

    summaries = _summary_rows_from_sql(
        path,
        limit=10,
        networks=["robinhood-testnet"],
        chain_ids=[46630],
        market_ids=None,
        category="not_executable",
        table="cow_execution_reviews",
    )

    match = next(item for item in summaries if int(item["id"]) == row_id)
    assert "NoProfitableRoute" in match["selectedProfitMissingReason"]
    assert "netProductDelta=-1" in match["selectedProfitMissingReason"]


def test_zero_selected_profit_is_unavailable_and_historical_overlay_is_read_only(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    row = _attempt(
        state="quote_required",
        phase="market_candidate",
        checks_passed=False,
        can_submit_order=False,
    )
    row["selectedProfitAmount"] = 0.0
    row["selectedProfitAmountSymbol"] = "ETH"
    row["selectedProfitSource"] = "native_contract_conservative_zero"
    row_id = record_cow_execution_attempts(path, [row])[0]

    review_row = _query_row(path, COW_EXECUTION_REVIEW_TABLE, row_id)
    assert review_row["selected_profit_amount"] is None
    assert review_row["selected_profit_source"] == "concrete_pnl_unavailable"
    assert "zero" in review_row["selected_profit_missing_reason"]

    # Simulate an old row written before the zero-value contract was enforced.
    with db_connection(path) as connection:
        connection.execute(
            "UPDATE cow_execution_reviews SET selected_profit_amount = 0.0, "
            "selected_profit_source = 'native_contract_conservative_zero', "
            "summary_json = ? WHERE id = ?",
            (
                json.dumps(
                    {
                        "id": row_id,
                        "selectedProfitAmount": 0.0,
                        "selectedProfitSource": "native_contract_conservative_zero",
                    }
                ),
                row_id,
            ),
        )

    stale = _attempt(
        state="quote_required",
        phase="market_candidate",
        checks_passed=False,
        can_submit_order=False,
    )
    stale["quote"] = {
        "live_onchain_evidence": {
            "blockers": ["chain review failed: NoProfitableRoute()"],
            "costModel": {
                "contractNetProductDelta": -0.123,
            },
        }
    }
    stale_id = record_cow_execution_attempts(path, [stale])[0]
    with db_connection(path) as connection:
        connection.execute(
            "UPDATE cow_execution_reviews SET summary_json = ?, "
            "selected_profit_amount = NULL WHERE id = ?",
            (
                json.dumps({"id": stale_id, "selectedProfitAmount": None}),
                stale_id,
            ),
        )

    from core.db.storage_cow_execution import _summary_rows_from_sql

    summaries = _summary_rows_from_sql(
        path,
        limit=10,
        networks=["robinhood-testnet"],
        chain_ids=[46630],
        market_ids=None,
        category="not_executable",
        table="cow_execution_reviews",
    )
    stale_summary = next(
        item for item in summaries if int(item["id"]) == stale_id
    )
    zero_summary = next(item for item in summaries if int(item["id"]) == row_id)
    assert zero_summary["selectedProfitAmount"] is None
    assert zero_summary["selectedProfitSource"] == "concrete_pnl_unavailable"
    assert "zero is not accepted" in zero_summary["selectedProfitMissingReason"]
    assert stale_summary["selectedProfitAmount"] is None
    assert (
        stale_summary["selectedProfitSource"]
        == "concrete_pnl_unavailable"
    )

    with db_connection(path) as connection:
        db_amount = connection.execute(
            "SELECT selected_profit_amount FROM cow_execution_reviews WHERE id = ?",
            (row_id,),
        ).fetchone()["selected_profit_amount"]
        stale_db_amount = connection.execute(
            "SELECT selected_profit_amount FROM cow_execution_reviews WHERE id = ?",
            (stale_id,),
        ).fetchone()["selected_profit_amount"]
    assert db_amount == 0.0
    assert stale_db_amount is None


def test_missing_pool_candidate_review_keeps_missing_concrete_pnl(tmp_path):
    path = str(tmp_path / "kv.sqlite3")
    row = _attempt(
        state="review_failed",
        phase="review",
        checks_passed=False,
        can_submit_order=False,
    )
    row["reviewFailureKind"] = "native_task_config_unavailable"
    row["reviewFailureReasons"] = ["missing_pool_candidates_or_token_metadata"]
    row["blocked_reasons"] = [
        "review_failed:missing_pool_candidates_or_token_metadata",
    ]

    compact = compact_cow_execution_attempt_row(row)

    assert compact["selectedProfitAmount"] is None
    assert (
        compact["selectedProfitSource"]
        == "concrete_pnl_unavailable"
    )
    assert "missing_pool_candidates_or_token_metadata" in compact[
        "selectedProfitMissingReason"
    ]
