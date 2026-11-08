#!/usr/bin/env python3
"""Plan the closed-loop runtime identity migration without writing SQLite.

The legacy runtime table stores one directed symbol key per unordered Y/Z
group.  The durable schema requires one physical address-path ``loopKey`` per
directed closed loop.  This command validates the canonical audit report and
database snapshot, then emits a deterministic migration plan containing:

* one archive plan for each legacy symbol-group runtime row;
* one re-key plan for the direction represented by the legacy row;
* one insert plan for the opposite direction;
* one target ``offchain_pending`` state for every directed closed loop.

P0-2.4 is a dry-run-only contract.  Production apply remains in P0-2.6.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "flashloan" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.runtime.closed_loop_identity import (  # noqa: E402
    directed_symbol_key_from_symbols,
    normalize_physical_loop_key,
    physical_loop_key_from_addresses,
    worker_group_key_from_symbols,
)


SCHEMA = "closed-loop-runtime-identity-migration-v1"
AUDIT_SCHEMA = "closed-loop-runtime-sync-audit-v1"
TASK = "P0-2.4"
CLOSED_TABLE = "v4_closed_loops"
RUNTIME_TABLE = "v4_closed_loop_runtime_state"
TARGET_STATE = "offchain_pending"
STATE_SOURCE = "identity_migration_reset"
ARCHIVE_DISPOSITION = "legacy_symbol_runtime_state_archived_before_rekey"

CLOSED_REQUIRED_COLUMNS = {
    "network",
    "chain_id",
    "protocol",
    "market_id",
    "loop_key",
    "path_addresses_json",
    "path_symbols_json",
}
RUNTIME_REQUIRED_COLUMNS = {
    "network",
    "chain_id",
    "protocol",
    "market_id",
    "loop_key",
    "state",
    "updated_at",
}


class ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(3, f"{self.prog}: error: {message}\n")


class MigrationContractError(RuntimeError):
    """The input cannot produce a safe migration plan."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _slug(value: Any, separator: str) -> str:
    text = _normalize_text(value)
    text = re.sub(r"[^a-z0-9]+", separator, text)
    return re.sub(rf"{re.escape(separator)}+", separator, text).strip(separator)


def _canonical_scope(values: dict[str, Any]) -> dict[str, Any]:
    raw_chain_id = values.get("chain_id", values.get("chainId"))
    try:
        chain_id = int(raw_chain_id)
    except (TypeError, ValueError) as exc:
        raise MigrationContractError("chain-id must be a positive integer") from exc
    if chain_id <= 0:
        raise MigrationContractError("chain-id must be a positive integer")
    scope = {
        "network": _slug(values.get("network"), "-"),
        "chainId": chain_id,
        "protocol": _slug(values.get("protocol"), "_"),
        "marketId": _slug(
            values.get("market_id", values.get("marketId")),
            "-",
        ),
    }
    if not all(scope.values()):
        raise MigrationContractError(
            "network, chain-id, protocol, and market-id are required"
        )
    return scope


def _scope_tuple(scope: dict[str, Any]) -> tuple[str, int, str, str]:
    return (
        str(scope["network"]),
        int(scope["chainId"]),
        str(scope["protocol"]),
        str(scope["marketId"]),
    )


def _row_scope(row: dict[str, Any]) -> tuple[str, int, str, str]:
    try:
        chain_id = int(row.get("chain_id"))
    except (TypeError, ValueError):
        chain_id = 0
    return (
        _slug(row.get("network"), "-"),
        chain_id,
        _slug(row.get("protocol"), "_"),
        _slug(row.get("market_id"), "-"),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_path(value: Any) -> tuple[list[str], str | None]:
    if value in (None, "", []):
        return [], "empty"
    if isinstance(value, list):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return [], "invalid-json"
    else:
        return [], "invalid-type"
    if not isinstance(parsed, list):
        return [], "not-array"
    parts = [_normalize_text(item) for item in parsed if _normalize_text(item)]
    if len(parts) != len(parsed):
        return parts, "empty-component"
    return parts, None


def _route_key_id(scope: dict[str, Any], addresses: list[str]) -> str | None:
    if len(addresses) != 4 or addresses[0] != addresses[-1]:
        return None
    tokens = [
        f"{scope['network']}:{scope['chainId']}:{address}"
        for address in addresses
    ]
    return f"route:v1:{scope['chainId']}:" + ":".join(tokens)


def _audit_compact_key(value: Any) -> str | None:
    """Match the audit script's diagnostic loop-key compaction.

    The audit deliberately keeps three-part non-address keys in its physical
    diagnostic comparison so legacy symbol groups are visible as orphans.
    Migration target keys still require ``normalize_physical_loop_key``.
    """
    text = _normalize_text(value)
    if not text or text.startswith("route:v1:"):
        return None
    parts = [part.strip() for part in re.split(r"[|:]", text) if part.strip()]
    if len(parts) == 4 and parts[0] == parts[-1]:
        parts = parts[:3]
    if len(parts) != 3:
        return None
    return "|".join(parts)


def _finding(
    code: str,
    severity: str,
    summary: str,
    *,
    count: int | None = None,
    keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "summary": summary,
    }
    if count is not None:
        payload["count"] = int(count)
    if keys:
        payload["keys"] = sorted(set(keys))[:100]
    return payload


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _require_table(
    connection: sqlite3.Connection,
    table: str,
    required_columns: set[str],
) -> set[str]:
    if not _table_exists(connection, table):
        raise MigrationContractError(f"required table is missing: {table}")
    columns = _table_columns(connection, table)
    missing = sorted(required_columns - columns)
    if missing:
        raise MigrationContractError(
            f"required columns are missing from {table}: {', '.join(missing)}"
        )
    return columns


def _open_read_transaction(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def _database_metadata(connection: sqlite3.Connection, path: Path) -> dict[str, Any]:
    quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
    return {
        "path": str(path),
        "sha256": _file_sha256(path),
        "sizeBytes": path.stat().st_size,
        "sqliteVersion": sqlite3.sqlite_version,
        "dataVersion": int(connection.execute("PRAGMA data_version").fetchone()[0]),
        "schemaVersion": int(connection.execute("PRAGMA schema_version").fetchone()[0]),
        "journalMode": str(connection.execute("PRAGMA journal_mode").fetchone()[0]),
        "quickCheck": str(quick_check),
        "transaction": "single-read-transaction-query-only",
    }


def _read_rows(
    connection: sqlite3.Connection,
    table: str,
    *,
    table_columns: set[str],
) -> list[dict[str, Any]]:
    order_columns = [
        column
        for column in (
            "network",
            "chain_id",
            "protocol",
            "market_id",
            "loop_key",
        )
        if column in table_columns
    ]
    order_sql = f" ORDER BY {', '.join(order_columns)}" if order_columns else ""
    rows = connection.execute(f"SELECT * FROM {table}{order_sql}").fetchall()
    return [dict(row) for row in rows]


def _load_source_report(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise MigrationContractError(f"source report does not exist: {path}")
    digest = _file_sha256(path)
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MigrationContractError(f"source report is not valid JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise MigrationContractError("source report root must be an object")
    if report.get("schema") != AUDIT_SCHEMA:
        raise MigrationContractError(
            f"source report schema must be {AUDIT_SCHEMA}"
        )
    if report.get("keyMode") != "directed-symbol":
        raise MigrationContractError(
            "source report keyMode must be directed-symbol"
        )
    if not isinstance(report.get("scope"), dict):
        raise MigrationContractError("source report scope is missing")
    return report, digest


def _source_scope(report: dict[str, Any]) -> dict[str, Any]:
    try:
        return _canonical_scope(report["scope"])
    except (KeyError, TypeError) as exc:
        raise MigrationContractError("source report scope is invalid") from exc


def _set_from_report(report: dict[str, Any], key: str) -> set[str]:
    selected = report.get("selectedCoverage")
    if not isinstance(selected, dict) or not isinstance(selected.get(key), list):
        raise MigrationContractError(f"source report selectedCoverage.{key} is missing")
    return {_normalize_text(value) for value in selected[key] if _normalize_text(value)}


def _coverage_from_rows(
    closed_records: list[dict[str, Any]],
    runtime_records: list[dict[str, Any]],
) -> dict[str, Any]:
    closed_directed = {record["directedKey"] for record in closed_records}
    runtime_directed = {record["directedKey"] for record in runtime_records}
    closed_physical = {record["auditPhysicalKey"] for record in closed_records}
    runtime_physical = {
        record["auditPhysicalKey"]
        for record in runtime_records
        if record.get("auditPhysicalKey")
    }
    closed_groups = {record["groupKey"] for record in closed_records}
    runtime_groups = {record["groupKey"] for record in runtime_records}
    return {
        "directedClosed": len(closed_directed),
        "directedRuntime": len(runtime_directed),
        "exactMatched": len(closed_directed & runtime_directed),
        "exactMissing": len(closed_directed - runtime_directed),
        "orphan": len(runtime_directed - closed_directed),
        "yzGroupMatched": len(closed_groups & runtime_groups),
        "yzGroupMissing": len(closed_groups - runtime_groups),
        "yzGroupOrphan": len(runtime_groups - closed_groups),
        "physicalLoopKeyMatched": len(closed_physical & runtime_physical),
        "physicalLoopKeyMissing": len(closed_physical - runtime_physical),
        "physicalLoopKeyOrphan": len(runtime_physical - closed_physical),
        "matchedKeys": sorted(closed_directed & runtime_directed),
        "missingKeys": sorted(closed_directed - runtime_directed),
        "orphanKeys": sorted(runtime_directed - closed_directed),
    }


def _validate_source_report(
    report: dict[str, Any],
    *,
    scope: dict[str, Any],
    database_sha256: str,
    coverage: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if _source_scope(report) != scope:
        findings.append(
            _finding(
                "SOURCE_REPORT_SCOPE_MISMATCH",
                "high",
                "Source report scope does not match the requested migration scope.",
            )
        )
    source_database = report.get("databaseMetadata")
    source_sha = (
        _normalize_text(source_database.get("sha256"))
        if isinstance(source_database, dict)
        else ""
    )
    if source_sha != database_sha256:
        findings.append(
            _finding(
                "SOURCE_REPORT_DATABASE_CHECKSUM_MISMATCH",
                "high",
                "Source report and migration database are not the same snapshot.",
            )
        )

    source_coverage = report.get("coverage")
    if not isinstance(source_coverage, dict):
        findings.append(
            _finding(
                "SOURCE_REPORT_COVERAGE_MISSING",
                "high",
                "Source report coverage object is missing.",
            )
        )
    else:
        compared_keys = (
            "directedClosed",
            "directedRuntime",
            "exactMatched",
            "exactMissing",
            "orphan",
            "yzGroupMatched",
            "yzGroupMissing",
            "yzGroupOrphan",
            "physicalLoopKeyMatched",
            "physicalLoopKeyMissing",
            "physicalLoopKeyOrphan",
        )
        mismatched = [
            key
            for key in compared_keys
            if int(source_coverage.get(key, -1)) != int(coverage[key])
        ]
        if mismatched:
            findings.append(
                _finding(
                    "SOURCE_REPORT_COVERAGE_MISMATCH",
                    "high",
                    "Source report coverage does not match the migration database.",
                    count=len(mismatched),
                    keys=mismatched,
                )
            )

    try:
        source_matched = _set_from_report(report, "matchedKeys")
        source_missing = _set_from_report(report, "missingKeys")
    except MigrationContractError as exc:
        findings.append(
            _finding(
                "SOURCE_REPORT_DIRECTION_SETS_MISSING",
                "high",
                str(exc),
            )
        )
    else:
        if source_matched != set(coverage["matchedKeys"]):
            findings.append(
                _finding(
                    "SOURCE_REPORT_MATCHED_KEYS_MISMATCH",
                    "high",
                    "Source report matched direction keys differ from the database.",
                    count=len(source_matched ^ set(coverage["matchedKeys"])),
                )
            )
        if source_missing != set(coverage["missingKeys"]):
            findings.append(
                _finding(
                    "SOURCE_REPORT_MISSING_KEYS_MISMATCH",
                    "high",
                    "Source report missing direction keys differ from the database.",
                    count=len(source_missing ^ set(coverage["missingKeys"])),
                )
            )
    return findings


def _closed_record(
    row: dict[str, Any],
    *,
    scope: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    findings: list[str] = []
    physical_key = normalize_physical_loop_key(row.get("loop_key"))
    if physical_key is None:
        findings.append("invalid_closed_loop_key")

    addresses, address_error = _decode_path(row.get("path_addresses_json"))
    if address_error:
        findings.append(f"address_path_{address_error}")
    physical_from_addresses = physical_loop_key_from_addresses(addresses)
    if physical_from_addresses is None:
        findings.append("unresolved_address")
    elif physical_key and physical_from_addresses != physical_key:
        findings.append("loop_key_address_mismatch")

    symbols, symbol_error = _decode_path(row.get("path_symbols_json"))
    if symbol_error:
        findings.append(f"symbol_path_{symbol_error}")
    directed_key = directed_symbol_key_from_symbols(symbols)
    group_key = worker_group_key_from_symbols(symbols)
    if directed_key is None:
        findings.append("unresolved_directed_symbol")
    if group_key is None:
        findings.append("unresolved_symbol_group")

    if findings or not physical_key or not directed_key or not group_key:
        return None, findings
    return {
        "row": row,
        "physicalKey": physical_key,
        "auditPhysicalKey": _audit_compact_key(row.get("loop_key")),
        "physicalFromAddresses": physical_from_addresses,
        "directedKey": directed_key,
        "groupKey": group_key,
        "routeKeyId": _route_key_id(scope, addresses),
        "addressError": address_error,
        "symbolError": symbol_error,
    }, findings


def _runtime_record(row: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    findings: list[str] = []
    raw_key = _normalize_text(row.get("loop_key"))
    physical_key = normalize_physical_loop_key(raw_key)
    if physical_key:
        findings.append("runtime_already_uses_physical_key")
    symbols = [part.strip() for part in raw_key.split("|") if part.strip()]
    directed_key = directed_symbol_key_from_symbols(symbols)
    group_key = worker_group_key_from_symbols(symbols)
    if directed_key is None:
        findings.append("unresolved_runtime_directed_symbol")
    if group_key is None:
        findings.append("unresolved_runtime_symbol_group")
    if findings or not directed_key or not group_key:
        return None, findings
    return {
        "row": row,
        "sourceRuntimeKey": raw_key,
        "directedKey": directed_key,
        "groupKey": group_key,
        "physicalKey": None,
        "auditPhysicalKey": _audit_compact_key(raw_key),
    }, findings


def _duplicate_keys(keys: Iterable[str]) -> list[str]:
    counts = Counter(keys)
    return sorted(key for key, count in counts.items() if count > 1)


def _target_row(
    *,
    scope: dict[str, Any],
    loop_key: str,
) -> dict[str, Any]:
    return {
        "network": scope["network"],
        "chain_id": scope["chainId"],
        "protocol": scope["protocol"],
        "market_id": scope["marketId"],
        "loop_key": loop_key,
        "state": TARGET_STATE,
        "generation": 1,
        "owner_thread": None,
        "tx_hash": None,
        "last_error": None,
        "review_cycle_id": None,
        "claimed_at": None,
        "heartbeat_at": None,
        "lease_until": None,
        "finished_at": None,
        "persisted_at": None,
    }


def _build_mapping(
    *,
    scope: dict[str, Any],
    closed_record: dict[str, Any],
    runtime_record: dict[str, Any],
    matched_closed_key: str,
    reverse_loop_key: str,
    migration_action: str,
    source_report_checksum: str,
) -> dict[str, Any]:
    return {
        "closedLoopKey": closed_record["physicalKey"],
        "routeKeyId": closed_record["routeKeyId"],
        "sourceSymbolGroupKey": runtime_record["groupKey"],
        "sourceRuntimeKey": runtime_record["sourceRuntimeKey"],
        "sourceRuntimeState": runtime_record["row"].get("state"),
        "sourceRuntimeUpdatedAt": runtime_record["row"].get("updated_at"),
        "sourceMatchedClosedLoopKey": matched_closed_key,
        "proposedRuntimeKey": closed_record["physicalKey"],
        "reverseLoopKey": reverse_loop_key,
        "migrationAction": migration_action,
        "proposedState": TARGET_STATE,
        "stateSource": STATE_SOURCE,
        "archiveDisposition": (
            ARCHIVE_DISPOSITION if migration_action == "rekey" else "none_new_direction"
        ),
        "sourceReportChecksum": source_report_checksum,
        "scope": scope,
        "findingCodes": [],
    }


def _build_plan(
    *,
    store: Path,
    source_report_path: Path,
    scope: dict[str, Any],
    expected_closed_loops: int,
    mode: str = "dry-run",
) -> dict[str, Any]:
    if expected_closed_loops <= 0 or expected_closed_loops % 2:
        raise MigrationContractError("expected-closed-loops must be a positive even integer")
    if not store.is_file():
        raise MigrationContractError(f"database does not exist: {store}")

    source_report, source_report_checksum = _load_source_report(source_report_path)
    connection = _open_read_transaction(store)
    try:
        database_metadata = _database_metadata(connection, store)
        closed_columns = _require_table(
            connection,
            CLOSED_TABLE,
            CLOSED_REQUIRED_COLUMNS,
        )
        runtime_columns = _require_table(
            connection,
            RUNTIME_TABLE,
            RUNTIME_REQUIRED_COLUMNS,
        )
        closed_all = _read_rows(
            connection,
            CLOSED_TABLE,
            table_columns=closed_columns,
        )
        runtime_all = _read_rows(
            connection,
            RUNTIME_TABLE,
            table_columns=runtime_columns,
        )
    finally:
        connection.rollback()
        connection.close()

    wanted = _scope_tuple(scope)
    closed_scope = [row for row in closed_all if _row_scope(row) == wanted]
    runtime_scope = [row for row in runtime_all if _row_scope(row) == wanted]
    out_of_scope_closed = [row for row in closed_all if _row_scope(row) != wanted]
    out_of_scope_runtime = [row for row in runtime_all if _row_scope(row) != wanted]

    findings = _validate_source_report(
        source_report,
        scope=scope,
        database_sha256=database_metadata["sha256"],
        coverage=_coverage_from_rows(
            [
                {
                    "directedKey": directed_symbol_key_from_symbols(
                        _decode_path(row.get("path_symbols_json"))[0]
                    ),
                    "physicalKey": normalize_physical_loop_key(row.get("loop_key")),
                    "auditPhysicalKey": _audit_compact_key(row.get("loop_key")),
                    "groupKey": worker_group_key_from_symbols(
                        _decode_path(row.get("path_symbols_json"))[0]
                    ),
                }
                for row in closed_scope
            ],
            [
                {
                    "directedKey": directed_symbol_key_from_symbols(
                        str(row.get("loop_key") or "").split("|")
                    ),
                    "physicalKey": normalize_physical_loop_key(row.get("loop_key")),
                    "auditPhysicalKey": _audit_compact_key(row.get("loop_key")),
                    "groupKey": worker_group_key_from_symbols(
                        str(row.get("loop_key") or "").split("|")
                    ),
                }
                for row in runtime_scope
            ],
        ),
    )

    closed_records: list[dict[str, Any]] = []
    closed_row_findings: list[str] = []
    for row in closed_scope:
        record, record_findings = _closed_record(row, scope=scope)
        closed_row_findings.extend(record_findings)
        if record is not None:
            closed_records.append(record)
    if closed_row_findings:
        findings.append(
            _finding(
                "CLOSED_LOOP_IDENTITY_INVALID",
                "high",
                "Closed-loop rows contain invalid or inconsistent address/symbol paths.",
                count=len(closed_row_findings),
                keys=closed_row_findings,
            )
        )

    runtime_records: list[dict[str, Any]] = []
    runtime_row_findings: list[str] = []
    for row in runtime_scope:
        record, record_findings = _runtime_record(row)
        runtime_row_findings.extend(record_findings)
        if record is not None:
            runtime_records.append(record)
    if runtime_row_findings:
        findings.append(
            _finding(
                "RUNTIME_IDENTITY_INVALID",
                "high",
                "Runtime rows cannot be parsed as legacy directed symbol identities.",
                count=len(runtime_row_findings),
                keys=runtime_row_findings,
            )
        )

    closed_physical_duplicates = _duplicate_keys(
        record["physicalKey"] for record in closed_records
    )
    closed_directed_duplicates = _duplicate_keys(
        record["directedKey"] for record in closed_records
    )
    runtime_directed_duplicates = _duplicate_keys(
        record["directedKey"] for record in runtime_records
    )
    runtime_group_duplicates = _duplicate_keys(
        record["groupKey"] for record in runtime_records
    )
    if closed_physical_duplicates:
        findings.append(
            _finding(
                "DUPLICATE_CLOSED_LOOP_KEY",
                "high",
                "Physical closed-loop keys are not unique.",
                count=len(closed_physical_duplicates),
                keys=closed_physical_duplicates,
            )
        )
    if closed_directed_duplicates:
        findings.append(
            _finding(
                "DUPLICATE_CLOSED_DIRECTED_KEY",
                "high",
                "Directed symbol keys are not unique.",
                count=len(closed_directed_duplicates),
                keys=closed_directed_duplicates,
            )
        )
    if runtime_directed_duplicates:
        findings.append(
            _finding(
                "DUPLICATE_RUNTIME_DIRECTED_KEY",
                "high",
                "Legacy runtime directed keys are not unique.",
                count=len(runtime_directed_duplicates),
                keys=runtime_directed_duplicates,
            )
        )
    if runtime_group_duplicates:
        findings.append(
            _finding(
                "DUPLICATE_RUNTIME_GROUP_KEY",
                "high",
                "Each legacy Y/Z group must have exactly one runtime row.",
                count=len(runtime_group_duplicates),
                keys=runtime_group_duplicates,
            )
        )

    actual_coverage = _coverage_from_rows(closed_records, runtime_records)
    if len(closed_scope) != expected_closed_loops:
        findings.append(
            _finding(
                "CLOSED_LOOP_COUNT_MISMATCH",
                "high",
                "Closed-loop row count does not match the migration contract.",
                count=len(closed_scope),
                keys=[f"expected={expected_closed_loops}"],
            )
        )
    expected_runtime_groups = expected_closed_loops // 2
    if len(runtime_scope) != expected_runtime_groups:
        findings.append(
            _finding(
                "RUNTIME_GROUP_COUNT_MISMATCH",
                "high",
                "Legacy runtime group count does not match the migration contract.",
                count=len(runtime_scope),
                keys=[f"expected={expected_runtime_groups}"],
            )
        )

    closed_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    runtime_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in closed_records:
        closed_by_group[record["groupKey"]].append(record)
    for record in runtime_records:
        runtime_by_group[record["groupKey"]].append(record)

    all_groups = sorted(set(closed_by_group) | set(runtime_by_group))
    direction_mappings: list[dict[str, Any]] = []
    archive_plans: list[dict[str, Any]] = []
    rekey_plans: list[dict[str, Any]] = []
    reverse_insert_plans: list[dict[str, Any]] = []
    unresolved_groups: list[str] = []

    for group_key in all_groups:
        group_loops = closed_by_group.get(group_key, [])
        group_runtimes = runtime_by_group.get(group_key, [])
        if len(group_loops) != 2 or len(group_runtimes) != 1:
            unresolved_groups.append(group_key)
            continue
        runtime_record = group_runtimes[0]
        matched_loops = [
            loop
            for loop in group_loops
            if loop["directedKey"] == runtime_record["directedKey"]
        ]
        if len(matched_loops) != 1:
            unresolved_groups.append(group_key)
            continue
        matched_loop = matched_loops[0]
        reverse_loops = [
            loop for loop in group_loops if loop["physicalKey"] != matched_loop["physicalKey"]
        ]
        if len(reverse_loops) != 1:
            unresolved_groups.append(group_key)
            continue
        reverse_loop = reverse_loops[0]
        rekey_mapping = _build_mapping(
            scope=scope,
            closed_record=matched_loop,
            runtime_record=runtime_record,
            matched_closed_key=matched_loop["physicalKey"],
            reverse_loop_key=reverse_loop["physicalKey"],
            migration_action="rekey",
            source_report_checksum=source_report_checksum,
        )
        reverse_mapping = _build_mapping(
            scope=scope,
            closed_record=reverse_loop,
            runtime_record=runtime_record,
            matched_closed_key=matched_loop["physicalKey"],
            reverse_loop_key=reverse_loop["physicalKey"],
            migration_action="insert",
            source_report_checksum=source_report_checksum,
        )
        direction_mappings.extend((rekey_mapping, reverse_mapping))
        rekey_plans.append(rekey_mapping)
        reverse_insert_plans.append(reverse_mapping)
        archive_plans.append(
            {
                "sourceSymbolGroupKey": runtime_record["groupKey"],
                "sourceRuntimeKey": runtime_record["sourceRuntimeKey"],
                "sourceRuntimeState": runtime_record["row"].get("state"),
                "sourceRuntimeUpdatedAt": runtime_record["row"].get("updated_at"),
                "sourceRuntimeRow": runtime_record["row"],
                "sourceReportChecksum": source_report_checksum,
                "scope": scope,
                "disposition": ARCHIVE_DISPOSITION,
            }
        )

    if unresolved_groups:
        findings.append(
            _finding(
                "UNRESOLVED_DIRECTION_GROUP",
                "high",
                "Each Y/Z group must contain exactly two loops and one exact runtime match.",
                count=len(unresolved_groups),
                keys=unresolved_groups,
            )
        )

    proposed_runtime_states = [
        _target_row(scope=scope, loop_key=mapping["proposedRuntimeKey"])
        for mapping in direction_mappings
    ]
    if any(
        mapping["proposedState"] != TARGET_STATE for mapping in direction_mappings
    ):
        findings.append(
            _finding(
                "TARGET_STATE_INVALID",
                "high",
                "Every proposed direction state must be offchain_pending.",
            )
        )
    if out_of_scope_closed or out_of_scope_runtime:
        findings.append(
            _finding(
                "OUT_OF_SCOPE_ROWS_EXCLUDED",
                "info",
                "Rows outside the requested scope were excluded from migration planning.",
                count=len(out_of_scope_closed) + len(out_of_scope_runtime),
            )
        )

    target_keys = [mapping["proposedRuntimeKey"] for mapping in direction_mappings]
    duplicate_target_keys = _duplicate_keys(target_keys)
    if duplicate_target_keys:
        findings.append(
            _finding(
                "DUPLICATE_TARGET_RUNTIME_KEY",
                "high",
                "Proposed runtime keys are not unique.",
                count=len(duplicate_target_keys),
                keys=duplicate_target_keys,
            )
        )

    expected_direction_mappings = expected_closed_loops
    expected_runtime_rows = expected_closed_loops
    expected_archive_plans = expected_closed_loops // 2
    expected_rekey_plans = expected_closed_loops // 2
    expected_reverse_inserts = expected_closed_loops // 2
    count_contract = {
        "closedLoops": len(closed_scope),
        "proposedDirectionMappings": len(direction_mappings),
        "proposedRuntimeStates": len(proposed_runtime_states),
        "archivePlans": len(archive_plans),
        "rekeyPlans": len(rekey_plans),
        "reverseInsertPlans": len(reverse_insert_plans),
        "sourceRuntimeGroups": len(runtime_scope),
        "sourceExactDirectedMatches": actual_coverage["exactMatched"],
        "unresolvedDirections": len(unresolved_groups),
        "duplicateDirectedKey": (
            len(closed_physical_duplicates)
            + len(closed_directed_duplicates)
            + len(runtime_directed_duplicates)
            + len(duplicate_target_keys)
        ),
        "unresolvedAddress": len(closed_row_findings),
        "crossScopeConflict": 0,
    }
    expected_counts = {
        "closedLoops": expected_closed_loops,
        "proposedDirectionMappings": expected_direction_mappings,
        "proposedRuntimeStates": expected_runtime_rows,
        "archivePlans": expected_archive_plans,
        "rekeyPlans": expected_rekey_plans,
        "reverseInsertPlans": expected_reverse_inserts,
        "sourceRuntimeGroups": expected_runtime_groups,
        "sourceExactDirectedMatches": expected_runtime_groups,
        "unresolvedDirections": 0,
        "duplicateDirectedKey": 0,
        "unresolvedAddress": 0,
        "crossScopeConflict": 0,
    }
    count_mismatches = [
        key
        for key, expected in expected_counts.items()
        if count_contract.get(key) != expected
    ]
    if count_mismatches:
        findings.append(
            _finding(
                "MIGRATION_COUNT_CONTRACT_FAILED",
                "high",
                "Migration plan counts do not satisfy the P0-2.4 contract.",
                count=len(count_mismatches),
                keys=count_mismatches,
            )
        )

    finding_codes = sorted({item["code"] for item in findings})
    high_findings = [item for item in findings if item["severity"] == "high"]
    exit_code = 2 if high_findings else 0
    return {
        "schema": SCHEMA,
        "generatedAt": _utc_now(),
        "task": TASK,
        "mode": mode,
        "scope": scope,
        "expectedClosedLoops": expected_closed_loops,
        "source": {
            "reportPath": str(source_report_path),
            "reportChecksum": source_report_checksum,
            "reportGeneratedAt": source_report.get("generatedAt"),
            "reportSchema": source_report.get("schema"),
            "reportKeyMode": source_report.get("keyMode"),
            "databaseMetadata": source_report.get("databaseMetadata"),
        },
        "databaseMetadata": database_metadata,
        "counts": count_contract,
        "coverage": {
            key: value
            for key, value in actual_coverage.items()
            if key not in {"matchedKeys", "missingKeys", "orphanKeys"}
        },
        "sourceCoverage": {
            "matchedKeys": sorted(source_report.get("selectedCoverage", {}).get("matchedKeys", [])),
            "missingKeys": sorted(source_report.get("selectedCoverage", {}).get("missingKeys", [])),
        },
        "outOfScope": {
            "closedLoops": len(out_of_scope_closed),
            "runtimeStates": len(out_of_scope_runtime),
        },
        "archivePlans": archive_plans,
        "rekeyPlans": rekey_plans,
        "reverseInsertPlans": reverse_insert_plans,
        "directionMappings": direction_mappings,
        "proposedRuntimeStates": proposed_runtime_states,
        "findings": findings,
        "findingCodes": finding_codes,
        "exitCode": exit_code,
    }


def _error_report(
    *,
    store: Path,
    source_report_path: Path,
    scope: dict[str, Any] | None,
    error: str,
    exit_code: int,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generatedAt": _utc_now(),
        "task": TASK,
        "mode": "dry-run",
        "scope": scope or {},
        "source": {
            "reportPath": str(source_report_path),
        },
        "databaseMetadata": {
            "path": str(store),
        },
        "counts": {},
        "archivePlans": [],
        "rekeyPlans": [],
        "reverseInsertPlans": [],
        "directionMappings": [],
        "proposedRuntimeStates": [],
        "findings": [
            {
                "code": "MIGRATION_CONTRACT_ERROR",
                "severity": "high",
                "summary": error,
            }
        ],
        "findingCodes": ["MIGRATION_CONTRACT_ERROR"],
        "error": error,
        "exitCode": exit_code,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = ContractArgumentParser(
        description="Plan closed-loop runtime direction identity migration."
    )
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--source-report", required=True, type=Path)
    parser.add_argument("--network", required=True)
    parser.add_argument("--chain-id", required=True, type=int)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--market-id", required=True)
    parser.add_argument("--expected-closed-loops", type=int, default=192)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run flag. Dry-run is also the default.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Reserved for P0-2.6. Rejected by the P0-2.4 contract.",
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.output.exists():
        print(f"output already exists: {args.output}", file=sys.stderr)
        return 3
    if args.apply:
        print("--apply is not available in P0-2.4; use a P0-2.6 implementation", file=sys.stderr)
        return 3

    scope: dict[str, Any] | None = None
    try:
        scope = _canonical_scope(
            {
                "network": args.network,
                "chain_id": args.chain_id,
                "protocol": args.protocol,
                "market_id": args.market_id,
            }
        )
        report = _build_plan(
            store=args.store,
            source_report_path=args.source_report,
            scope=scope,
            expected_closed_loops=args.expected_closed_loops,
            mode="dry-run",
        )
    except MigrationContractError as exc:
        report = _error_report(
            store=args.store,
            source_report_path=args.source_report,
            scope=scope,
            error=str(exc),
            exit_code=3,
        )
    except Exception as exc:
        report = _error_report(
            store=args.store,
            source_report_path=args.source_report,
            scope=scope,
            error=f"{type(exc).__name__}: {exc}",
            exit_code=4,
        )

    try:
        _write_report(args.output, report)
    except OSError as exc:
        print(f"failed to write report: {exc}", file=sys.stderr)
        return 4

    counts = report.get("counts") or {}
    print(
        "wrote {path} exit={exit_code} closedLoops={closed} mappings={mappings} "
        "rekey={rekey} reverseInsert={reverse} findings={findings}".format(
            path=args.output,
            exit_code=report.get("exitCode"),
            closed=counts.get("closedLoops", "-"),
            mappings=counts.get("proposedDirectionMappings", "-"),
            rekey=counts.get("rekeyPlans", "-"),
            reverse=counts.get("reverseInsertPlans", "-"),
            findings=len(report.get("findingCodes") or []),
        )
    )
    return int(report.get("exitCode") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
