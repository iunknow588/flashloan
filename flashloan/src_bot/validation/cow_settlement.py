from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any


SUCCESS_STATES = {
    "settled",
    "settlement_success",
    "confirmed_success",
    "execution_success",
    "success",
}


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _nested(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def normalize_cow_attempt(row: dict[str, Any]) -> dict[str, Any]:
    sdk = row.get("cow_sdk_result") if isinstance(row.get("cow_sdk_result"), dict) else {}
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    quote_call = quote.get("quote_call") if isinstance(quote.get("quote_call"), dict) else {}
    submission = row.get("submission") if isinstance(row.get("submission"), dict) else {}
    state = str(_nested(row, "state", "status") or sdk.get("status") or "").strip().lower()
    order_id = _nested(row, "order_id", "orderId") or sdk.get("submission_order_id") or submission.get("orderId")
    settled = state in SUCCESS_STATES or str(sdk.get("settlement_status") or "").lower() in SUCCESS_STATES
    quote_at = _datetime(
        _nested(quote_call, "finishedAt", "finished_at")
        or _nested(row, "quoted_at", "quote_at")
        or row.get("observed_at")
    )
    submitted_at = _datetime(
        _nested(row, "submitted_at", "submission_at")
        or _nested(submission, "submittedAt", "submitted_at")
        or _nested(row.get("submit_call") if isinstance(row.get("submit_call"), dict) else {}, "finishedAt", "finished_at")
    )
    settled_at = _datetime(
        _nested(row, "settled_at", "settlement_at")
        or _nested(sdk, "settled_at", "settlement_at")
        or row.get("finished_at")
    )
    return {
        "state": state,
        "quote_ok": state not in {"quote_failed", "quote_unavailable"} and bool(quote_at),
        "order_id": order_id,
        "submitted": bool(order_id) or state in {"submitted_success", "submission_success"},
        "settled": settled,
        "quote_at": quote_at,
        "submitted_at": submitted_at,
        "settled_at": settled_at,
    }


def summarize_cow_attempts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = [normalize_cow_attempt(row) for row in rows if isinstance(row, dict)]
    quote_rows = [row for row in normalized if row["quote_ok"]]
    submitted_rows = [row for row in quote_rows if row["submitted"]]
    settled_rows = [row for row in quote_rows if row["settled"]]
    submit_delays = [
        (row["submitted_at"] - row["quote_at"]).total_seconds()
        for row in submitted_rows
        if row["quote_at"] and row["submitted_at"] and row["submitted_at"] >= row["quote_at"]
    ]
    settlement_delays = [
        (row["settled_at"] - row["quote_at"]).total_seconds()
        for row in settled_rows
        if row["quote_at"] and row["settled_at"] and row["settled_at"] >= row["quote_at"]
    ]
    # A row count alone is not evidence that the quote/settlement hypothesis
    # can be measured. Quote failures and submission-disabled records must stay
    # explicitly insufficient instead of looking like a completed analysis.
    status = "no_data" if not rows else "insufficient_data"
    if quote_rows and settled_rows:
        status = "calculated"
    return {
        "status": status,
        "attempt_count": len(rows),
        "quote_success_count": len(quote_rows),
        "submitted_count": len(submitted_rows),
        "settled_count": len(settled_rows),
        "quote_to_submit_rate": len(submitted_rows) / len(quote_rows) if quote_rows else None,
        "quote_to_settlement_rate": len(settled_rows) / len(quote_rows) if quote_rows else None,
        "quote_to_submit_delay_seconds": _delay_summary(submit_delays),
        "quote_to_settlement_delay_seconds": _delay_summary(settlement_delays),
        "missing_settlement_timestamp_count": sum(1 for row in settled_rows if not row["settled_at"]),
        "data_quality": {
            "quote_evidence_available": bool(quote_rows),
            "settlement_evidence_available": bool(settled_rows),
            "missing_settlement_evidence": bool(quote_rows) and not bool(settled_rows),
        },
    }


def _delay_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"status": "missing_timestamps", "sample_count": 0, "p50": None, "p90": None}
    ordered = sorted(values)
    p90_index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.9)))
    return {
        "status": "calculated",
        "sample_count": len(values),
        "p50": median(values),
        "p90": ordered[p90_index],
    }
