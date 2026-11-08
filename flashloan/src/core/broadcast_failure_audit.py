from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

SCHEMA_VERSION = "robinhood-testnet-broadcast-failure-audit-v1"
SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_PATH = (
    SRC_ROOT
    / "runtime"
    / "evidence"
    / "robinhood-testnet"
    / "broadcast_failure_history"
    / "closed_loop_broadcast_failures.jsonl"
)


def broadcast_failure_audit_path() -> Path:
    return DEFAULT_AUDIT_PATH


def append_broadcast_failure_audit(record: dict[str, Any]) -> None:
    path = broadcast_failure_audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        logger.warning("broadcast failure audit write failed: %s", exc)


def load_broadcast_failure_audit_records(
    path: Path | None = None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    audit_path = path or broadcast_failure_audit_path()
    if not audit_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    if limit is not None and limit >= 0:
        rows = rows[-int(limit):]
    return rows


def build_auto_disposition_audit_record(
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    scope_key: str,
    error_key: str,
    error_code: int | None,
    error_name: str,
    disposition: str,
    raw_error: str,
    attempt: int,
    generation: int,
    owner: str,
    next_state: str,
    next_retry_at: str | None = None,
    isolation_reason: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Build an audit record for an automatically dispatched error outcome."""
    recorded = recorded_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "schemaVersion": "robinhood-testnet-broadcast-failure-audit-v2",
        "recordedAt": recorded,
        "network": network,
        "chainId": chain_id,
        "protocol": protocol,
        "marketId": market_id,
        "scopeKey": scope_key,
        "errorKey": error_key,
        "errorCode": error_code,
        "errorName": error_name,
        "disposition": disposition,
        "rawError": raw_error,
        "attempt": int(attempt),
        "generation": int(generation),
        "owner": owner,
        "nextState": next_state,
        "nextRetryAt": next_retry_at,
        "isolationReason": isolation_reason,
    }
