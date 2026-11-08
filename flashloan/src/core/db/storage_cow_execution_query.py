from __future__ import annotations

import json
from typing import Any

from core.db.storage_common import db_connection
from core.db.cow_execution_mapping import SELECTED_PROFIT_CAMEL_KEYS
from core.db.storage_cow_execution_compact import compact_cow_execution_attempt_row
from core.db.storage_cow_policy import (
    _decorate_attempt_review,
    cow_execution_attempt_category,
)
from core.db.storage_cow_execution_schema import _COW_ATTEMPT_TABLE


def _row_scope_values(payload: dict[str, Any]) -> tuple[str, int, str, str, str, str, str]:
    market_state = payload.get("market_state") if isinstance(payload.get("market_state"), dict) else {}
    cow_filter = market_state.get("cow_filter") if isinstance(market_state.get("cow_filter"), dict) else {}
    network = str(
        payload.get("network")
        or payload.get("cow_network")
        or market_state.get("cow_network")
        or cow_filter.get("network")
        or ""
    ).strip().lower()
    try:
        chain_id = int(
            payload.get("chain_id")
            or payload.get("cow_chain_id")
            or market_state.get("cow_chain_id")
            or cow_filter.get("chain_id")
            or 0
        )
    except (TypeError, ValueError):
        chain_id = 0
    market_id = str(payload.get("market_id") or (payload.get("quote") or {}).get("market_id") or "").strip().lower()
    pair = str(payload.get("pair") or payload.get("symbol_pair") or "").strip()
    tx_hash = str(
        payload.get("tx_hash")
        or payload.get("transaction_hash")
        or (payload.get("quote") or {}).get("tx_hash")
        or (payload.get("quote") or {}).get("transaction_hash")
        or ""
    ).strip().lower()
    created_at = str(payload.get("created_at") or payload.get("observed_at") or payload.get("updated_at") or "")
    category = cow_execution_attempt_category(payload)
    return network, chain_id, market_id, pair, tx_hash, created_at, category


def _scope_sql(
    *,
    networks: list[str] | None,
    chain_ids: list[int] | None,
    market_ids: list[str] | None,
    category: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    wanted_networks = {str(item).strip().lower() for item in networks or [] if str(item).strip()}
    wanted_chain_ids = {int(item) for item in chain_ids or [] if item is not None}
    wanted_market_ids = {str(item).strip().lower() for item in market_ids or [] if str(item).strip()}
    if wanted_networks:
        placeholders = ",".join("?" for _ in wanted_networks)
        clauses.append(f"network IN ({placeholders})")
        params.extend(sorted(wanted_networks))
    if wanted_chain_ids:
        placeholders = ",".join("?" for _ in wanted_chain_ids)
        clauses.append(f"chain_id IN ({placeholders})")
        params.extend(sorted(wanted_chain_ids))
    if wanted_market_ids:
        placeholders = ",".join("?" for _ in wanted_market_ids)
        clauses.append(f"market_id IN ({placeholders})")
        params.extend(sorted(wanted_market_ids))
    if category:
        clauses.append("category = ?")
        params.append(str(category).strip().lower())
    if start_at:
        clauses.append("created_at >= ?")
        params.append(str(start_at))
    if end_at:
        clauses.append("created_at <= ?")
        params.append(str(end_at))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def _payload_rows_from_sql(
    database_url: str,
    *,
    limit: int,
    networks: list[str] | None,
    chain_ids: list[int] | None,
    market_ids: list[str] | None,
    category: str | None,
    start_at: str | None = None,
    end_at: str | None = None,
    table: str = _COW_ATTEMPT_TABLE,
) -> list[dict[str, Any]]:
    where, params = _scope_sql(
        networks=networks,
        chain_ids=chain_ids,
        market_ids=market_ids,
        category=category,
        start_at=start_at,
        end_at=end_at,
    )
    sql = (
        f"SELECT payload_json, id, created_at FROM {table}"
        f"{where} ORDER BY created_at DESC, id DESC LIMIT ?"
    )
    params = [*params, max(1, int(limit))]
    with db_connection(database_url) as connection:
        rows = connection.execute(sql, params).fetchall()
    payloads = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload["id"] = int(row["id"])
            payloads.append(payload)
    return payloads


def _summary_rows_from_sql(
    database_url: str,
    *,
    limit: int,
    offset: int = 0,
    networks: list[str] | None,
    chain_ids: list[int] | None,
    market_ids: list[str] | None,
    category: str | None,
    table: str = _COW_ATTEMPT_TABLE,
) -> list[dict[str, Any]]:
    where, params = _scope_sql(
        networks=networks,
        chain_ids=chain_ids,
        market_ids=market_ids,
        category=category,
    )
    sql = (
        f"SELECT summary_json, payload_json, id, created_at FROM {table}"
        f"{where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    )
    params = [*params, max(1, int(limit)), max(0, int(offset))]
    with db_connection(database_url) as connection:
        rows = connection.execute(sql, params).fetchall()
    summaries: list[dict[str, Any]] = []
    summary_by_id: dict[int, dict[str, Any]] = {}
    fallback_ids: list[int] = []
    enrich_ids: list[int] = []
    amount_overlay_ids: list[int] = []
    generic_markers = (
        "no_native_signed_profit",
        "native result has no signed borrow-token profit value",
        "zero placeholder does not prove",
    )

    def _zero_amount(value: Any) -> bool:
        try:
            return value is not None and float(value) == 0.0
        except (TypeError, ValueError):
            return False

    for row in rows:
        row_id = int(row["id"])
        summary_text = str(row["summary_json"] or "").strip()
        summary: dict[str, Any] | None = None
        if summary_text:
            try:
                parsed = json.loads(summary_text)
            except (TypeError, json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, dict):
                summary = parsed
        if summary is None:
            fallback_ids.append(row_id)
            continue
        summary["id"] = row_id
        if not summary.get("created_at"):
            summary["created_at"] = str(row["created_at"] or "")
        reason = str(summary.get("selectedProfitMissingReason") or "").strip()
        if (
            not reason
            or any(marker in reason for marker in generic_markers)
            or _zero_amount(summary.get("selectedProfitAmount"))
            or summary.get("selectedProfitSource") == "native_contract_conservative_zero"
        ):
            enrich_ids.append(row_id)
        if (
            summary.get("selectedProfitAmount") is None
            or _zero_amount(summary.get("selectedProfitAmount"))
            or summary.get("selectedProfitSource") == "native_contract_conservative_zero"
        ):
            amount_overlay_ids.append(row_id)
        summary_by_id[row_id] = summary
        summaries.append(summary)
    source_ids = sorted(set(fallback_ids + enrich_ids + amount_overlay_ids))
    if not source_ids:
        summaries.sort(
            key=lambda item: (
                str(item.get("created_at") or item.get("observed_at") or ""),
                int(item.get("id") or 0),
            ),
            reverse=True,
        )
        return summaries
    source_placeholders = ",".join("?" for _ in source_ids)
    with db_connection(database_url) as connection:
        source_rows = connection.execute(
            f"SELECT id, payload_json, created_at FROM {table} WHERE id IN ({source_placeholders})",
            source_ids,
        ).fetchall()
    payload_by_id: dict[int, dict[str, Any]] = {}
    compact_by_id: dict[int, dict[str, Any]] = {}
    created_at_by_id: dict[int, str] = {}
    for row in source_rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        current_id = int(row["id"])
        payload_by_id[current_id] = payload
        created_at_by_id[current_id] = str(row["created_at"] or "")
        compact_by_id[current_id] = compact_cow_execution_attempt_row(
            _decorate_attempt_review(payload)
        )
    for row_id in fallback_ids:
        payload = payload_by_id.get(row_id)
        if not isinstance(payload, dict):
            continue
        summary = compact_cow_execution_attempt_row(
            _decorate_attempt_review(payload)
        )
        summary["id"] = row_id
        if not summary.get("created_at"):
            summary["created_at"] = created_at_by_id.get(row_id, "")
        summary_by_id[row_id] = summary
        summaries.append(summary)
    for row_id in sorted(set(enrich_ids + amount_overlay_ids)):
        summary = summary_by_id.get(row_id)
        compact = compact_by_id.get(row_id)
        if summary is None or compact is None:
            continue
        if row_id in amount_overlay_ids and (
            summary.get("selectedProfitAmount") is None
            or _zero_amount(summary.get("selectedProfitAmount"))
            or summary.get("selectedProfitSource") == "native_contract_conservative_zero"
        ):
            summary["selectedProfitAmount"] = None
            for key in SELECTED_PROFIT_CAMEL_KEYS:
                if key == "selectedProfitMissingReason":
                    continue
                value = compact.get(key)
                if value is not None:
                    summary[key] = value
        if row_id in enrich_ids:
            compact_reason = compact.get("selectedProfitMissingReason")
            if compact_reason:
                summary["selectedProfitMissingReason"] = compact_reason
    summaries = list(summary_by_id.values())
    summaries.sort(
        key=lambda item: (
            str(item.get("created_at") or item.get("observed_at") or ""),
            int(item.get("id") or 0),
        ),
        reverse=True,
    )
    return summaries


__all__ = [
    "_payload_rows_from_sql",
    "_row_scope_values",
    "_scope_sql",
    "_summary_rows_from_sql",
]
