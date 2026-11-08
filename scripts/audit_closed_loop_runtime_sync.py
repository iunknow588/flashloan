#!/usr/bin/env python3
"""Audit closed-loop identities against persisted runtime identities.

The audit is intentionally read-only. It opens SQLite with ``mode=ro``, enables
``query_only``, and executes all reads inside one transaction. The report keeps
directed symbols, unordered Y/Z groups, physical loop keys, and optional route
key IDs separate so an unordered group cannot be mistaken for bidirectional
coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


SCHEMA = "closed-loop-runtime-sync-audit-v1"
CLOSED_TABLE = "v4_closed_loops"
RUNTIME_TABLE = "v4_closed_loop_runtime_state"
KEY_MODES = ("loop-key", "route-key-id", "directed-symbol", "yz-group")
ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")

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
}


class ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(3, f"{self.prog}: error: {message}\n")


class AuditContractError(RuntimeError):
    """The input cannot produce a valid audit conclusion."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _slug(value: Any, separator: str) -> str:
    text = _normalize_text(value)
    text = re.sub(r"[^a-z0-9]+", separator, text)
    return re.sub(rf"{re.escape(separator)}+", separator, text).strip(separator)


def _canonical_network(value: Any) -> str:
    return _slug(value, "-")


def _canonical_protocol(value: Any) -> str:
    return _slug(value, "_")


def _canonical_market_id(value: Any) -> str:
    return _slug(value, "-")


def _canonical_scope(values: dict[str, Any]) -> dict[str, Any]:
    try:
        chain_id = int(values.get("chain_id"))
    except (TypeError, ValueError) as exc:
        raise AuditContractError("chain-id must be a positive integer") from exc
    if chain_id <= 0:
        raise AuditContractError("chain-id must be a positive integer")
    scope = {
        "network": _canonical_network(values.get("network")),
        "chainId": chain_id,
        "protocol": _canonical_protocol(values.get("protocol")),
        "marketId": _canonical_market_id(values.get("market_id")),
    }
    if not all(scope.values()):
        raise AuditContractError("network, chain-id, protocol, and market-id are required")
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
        _canonical_network(row.get("network")),
        chain_id,
        _canonical_protocol(row.get("protocol")),
        _canonical_market_id(row.get("market_id")),
    )


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


def _path_identity(parts: list[str], *, ordered: bool) -> str | None:
    if len(parts) == 4:
        if parts[0] != parts[-1]:
            return None
        parts = parts[:3]
    if len(parts) != 3 or any(not part for part in parts):
        return None
    if ordered:
        return "|".join(parts)
    return "|".join((parts[0], *sorted(parts[1:3])))


def _compact_loop_key(value: Any) -> str | None:
    text = _normalize_text(value)
    if not text or text.startswith("route:v1:"):
        return None
    parts = [part.strip() for part in re.split(r"[|:]", text) if part.strip()]
    if len(parts) == 4 and parts[0] == parts[-1]:
        parts = parts[:3]
    if len(parts) != 3:
        return None
    return "|".join(parts)


def _route_key_id(scope: dict[str, Any], addresses: list[str]) -> str | None:
    if len(addresses) != 4 or addresses[0] != addresses[-1]:
        return None
    if any(not ADDRESS_RE.fullmatch(address) for address in addresses):
        return None
    network = str(scope["network"])
    chain_id = int(scope["chainId"])
    tokens = [f"{network}:{chain_id}:{address}" for address in addresses]
    return f"route:v1:{chain_id}:" + ":".join(tokens)


def _closed_key(row: dict[str, Any], key_mode: str) -> tuple[str | None, str | None]:
    if key_mode == "loop-key":
        key = _compact_loop_key(row.get("loop_key"))
        return key, None if key else "invalid-loop-key"
    if key_mode == "directed-symbol":
        symbols, error = _decode_path(row.get("path_symbols_json"))
        key = _path_identity(symbols, ordered=True)
        return key, error or (None if key else "invalid-symbol-path")
    if key_mode == "yz-group":
        symbols, error = _decode_path(row.get("path_symbols_json"))
        key = _path_identity(symbols, ordered=False)
        return key, error or (None if key else "invalid-symbol-path")

    stored_route_key = _normalize_text(row.get("route_key_id"))
    if stored_route_key:
        return stored_route_key, None
    addresses, error = _decode_path(row.get("path_addresses_json"))
    key = _route_key_id(
        _scope_as_dict(_row_scope(row)),
        addresses,
    )
    return key, error or (None if key else "invalid-address-path")


def _runtime_key(row: dict[str, Any], key_mode: str) -> tuple[str | None, str | None]:
    if key_mode == "route-key-id":
        stored_route_key = _normalize_text(row.get("route_key_id"))
        loop_key = _normalize_text(row.get("loop_key"))
        key = stored_route_key or (loop_key if loop_key.startswith("route:v1:") else None)
        return key, None if key else "runtime-route-key-missing"
    key = _compact_loop_key(row.get("loop_key"))
    if key is None:
        return None, "invalid-runtime-loop-key"
    if key_mode == "yz-group":
        parts = key.split("|")
        return "|".join((parts[0], *sorted(parts[1:3]))), None
    return key, None


def _scope_as_dict(scope: tuple[str, int, str, str]) -> dict[str, Any]:
    return {
        "network": scope[0],
        "chainId": scope[1],
        "protocol": scope[2],
        "marketId": scope[3],
    }


def _pairs(rows: Iterable[dict[str, Any]], key_mode: str, *, runtime: bool) -> dict[str, Any]:
    pairs: list[tuple[str, dict[str, Any]]] = []
    invalid: list[dict[str, Any]] = []
    resolver = _runtime_key if runtime else _closed_key
    for row in rows:
        key, error = resolver(row, key_mode)
        if key:
            pairs.append((key, row))
        else:
            invalid.append({"reason": error or "invalid-key", "row": row})
    counts = Counter(key for key, _ in pairs)
    duplicates = [
        {"key": key, "count": count}
        for key, count in sorted(counts.items())
        if count > 1
    ]
    return {
        "pairs": pairs,
        "keys": set(counts),
        "invalid": invalid,
        "duplicates": duplicates,
    }


def _coverage(
    closed: dict[str, Any],
    runtime: dict[str, Any],
    *,
    duplicate_closed: int,
    duplicate_runtime: int,
) -> dict[str, Any]:
    closed_keys = set(closed["keys"])
    runtime_keys = set(runtime["keys"])
    matched = closed_keys & runtime_keys
    missing = closed_keys - runtime_keys
    orphan = runtime_keys - closed_keys
    return {
        "directedClosed": len(closed_keys),
        "directedRuntime": len(runtime_keys),
        "exactMatched": len(matched),
        "exactMissing": len(missing),
        "duplicateDirectedKey": duplicate_closed + duplicate_runtime,
        "orphan": len(orphan),
        "_matchedKeys": sorted(matched),
        "_missingKeys": sorted(missing),
        "_orphanKeys": sorted(orphan),
    }


def _public_coverage(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if not key.startswith("_")}


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
        raise AuditContractError(f"required table is missing: {table}")
    columns = _table_columns(connection, table)
    missing = sorted(required_columns - columns)
    if missing:
        raise AuditContractError(
            f"required columns are missing from {table}: {', '.join(missing)}"
        )
    return columns


def _select_columns(columns: set[str], required: Iterable[str], optional: Iterable[str]) -> str:
    selected = list(required)
    selected.extend(column for column in optional if column in columns)
    return ", ".join(selected)


def _read_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: set[str],
    required: set[str],
    *,
    optional: Iterable[str] = (),
) -> list[dict[str, Any]]:
    selected = _select_columns(columns, sorted(required), optional)
    rows = connection.execute(
        f"SELECT {selected} FROM {table} "
        "ORDER BY network, chain_id, protocol, market_id, loop_key"
    ).fetchall()
    return [dict(row) for row in rows]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        payload["count"] = count
    if keys:
        payload["keys"] = list(keys)[:100]
    return payload


def _scope_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, int] = Counter()
    for row in rows:
        scope = _row_scope(row)
        key = "|".join(str(value) for value in scope)
        grouped[key] += 1
    return {key: grouped[key] for key in sorted(grouped)}


def _run_audit(
    *,
    store: Path,
    scope: dict[str, Any],
    key_mode: str,
    all_scopes: bool,
) -> dict[str, Any]:
    if not store.is_file():
        raise AuditContractError(f"database does not exist: {store}")

    connection = _open_read_transaction(store)
    try:
        metadata = _database_metadata(connection, store)
        closed_columns = _require_table(connection, CLOSED_TABLE, CLOSED_REQUIRED_COLUMNS)
        runtime_columns = _require_table(connection, RUNTIME_TABLE, RUNTIME_REQUIRED_COLUMNS)
        closed_all = _read_rows(
            connection,
            CLOSED_TABLE,
            closed_columns,
            CLOSED_REQUIRED_COLUMNS,
            optional=("route_key_id", "status"),
        )
        runtime_all = _read_rows(
            connection,
            RUNTIME_TABLE,
            runtime_columns,
            RUNTIME_REQUIRED_COLUMNS,
            optional=(
                "route_key_id",
                "updated_at",
                "generation",
                "owner_thread",
                "last_error",
            ),
        )
    finally:
        connection.rollback()
        connection.close()

    wanted = _scope_tuple(scope)
    closed_scope = [
        row for row in closed_all if _row_scope(row) == wanted
    ] if not all_scopes else closed_all
    runtime_scope = [
        row for row in runtime_all if _row_scope(row) == wanted
    ] if not all_scopes else runtime_all
    out_of_scope_runtime = [
        row for row in runtime_all if _row_scope(row) != wanted
    ]
    out_of_scope_closed = [
        row for row in closed_all if _row_scope(row) != wanted
    ]
    scope_leak_rows = [
        row
        for row in closed_scope + runtime_scope
        if not all_scopes and _row_scope(row) != wanted
    ]

    closed_directed = _pairs(closed_scope, "directed-symbol", runtime=False)
    runtime_directed = _pairs(runtime_scope, "directed-symbol", runtime=True)
    directed_coverage = _coverage(
        closed_directed,
        runtime_directed,
        duplicate_closed=len(closed_directed["duplicates"]),
        duplicate_runtime=len(runtime_directed["duplicates"]),
    )

    closed_group = _pairs(closed_scope, "yz-group", runtime=False)
    runtime_group = _pairs(runtime_scope, "yz-group", runtime=True)
    group_coverage = _coverage(
        closed_group,
        runtime_group,
        duplicate_closed=len(closed_group["duplicates"]),
        duplicate_runtime=len(runtime_group["duplicates"]),
    )

    closed_physical = _pairs(closed_scope, "loop-key", runtime=False)
    runtime_physical = _pairs(runtime_scope, "loop-key", runtime=True)
    physical_coverage = _coverage(
        closed_physical,
        runtime_physical,
        duplicate_closed=len(closed_physical["duplicates"]),
        duplicate_runtime=len(runtime_physical["duplicates"]),
    )

    closed_selected = _pairs(closed_scope, key_mode, runtime=False)
    runtime_selected = _pairs(runtime_scope, key_mode, runtime=True)
    selected_coverage = _coverage(
        closed_selected,
        runtime_selected,
        duplicate_closed=len(closed_selected["duplicates"]),
        duplicate_runtime=len(runtime_selected["duplicates"]),
    )

    coverage = _public_coverage(selected_coverage)
    coverage.update(
        {
            "directedSymbolMatched": directed_coverage["exactMatched"],
            "directedSymbolMissing": directed_coverage["exactMissing"],
            "directedSymbolOrphan": directed_coverage["orphan"],
            "yzGroupMatched": group_coverage["exactMatched"],
            "yzGroupMissing": group_coverage["exactMissing"],
            "yzGroupOrphan": group_coverage["orphan"],
            "physicalLoopKeyMatched": physical_coverage["exactMatched"],
            "physicalLoopKeyMissing": physical_coverage["exactMissing"],
            "physicalLoopKeyOrphan": physical_coverage["orphan"],
            "outOfScopeRuntime": len(out_of_scope_runtime),
            "scopeLeak": len(scope_leak_rows),
        }
    )

    findings: list[dict[str, Any]] = []
    if closed_selected["invalid"]:
        findings.append(
            _finding(
                "CLOSED_LOOP_KEY_INVALID",
                "high",
                f"{len(closed_selected['invalid'])} closed-loop rows have no valid {key_mode} key.",
                count=len(closed_selected["invalid"]),
                keys=(item["reason"] for item in closed_selected["invalid"]),
            )
        )
    if runtime_selected["invalid"]:
        findings.append(
            _finding(
                "RUNTIME_KEY_INVALID",
                "high",
                f"{len(runtime_selected['invalid'])} runtime rows have no valid {key_mode} key.",
                count=len(runtime_selected["invalid"]),
                keys=(item["reason"] for item in runtime_selected["invalid"]),
            )
        )
    if selected_coverage["duplicateDirectedKey"]:
        findings.append(
            _finding(
                "DIRECTED_KEY_DUPLICATE",
                "high",
                "Canonical keys are not unique in the selected identity view.",
                count=selected_coverage["duplicateDirectedKey"],
            )
        )
    if selected_coverage["exactMissing"]:
        findings.append(
            _finding(
                "EXACT_KEY_MISSING",
                "high",
                "Closed loops are missing runtime identities in the selected view.",
                count=selected_coverage["exactMissing"],
                keys=selected_coverage["_missingKeys"],
            )
        )
    if selected_coverage["orphan"]:
        findings.append(
            _finding(
                "EXACT_KEY_ORPHAN",
                "high",
                "Runtime identities do not map to a closed loop in the selected view.",
                count=selected_coverage["orphan"],
                keys=selected_coverage["_orphanKeys"],
            )
        )
    if directed_coverage["exactMissing"] and not group_coverage["exactMissing"]:
        findings.append(
            _finding(
                "DIRECTION_COLLAPSE",
                "high",
                "Every unordered Y/Z group exists, but directed-symbol coverage is incomplete.",
                count=directed_coverage["exactMissing"],
            )
        )
    if out_of_scope_runtime or out_of_scope_closed:
        findings.append(
            _finding(
                "OUT_OF_SCOPE_ROWS_EXCLUDED",
                "info",
                "Rows outside the requested scope were listed separately and excluded from coverage.",
                count=len(out_of_scope_runtime) + len(out_of_scope_closed),
            )
        )
    if scope_leak_rows:
        findings.append(
            _finding(
                "SCOPE_LEAK",
                "high",
                "Rows from another scope entered the selected coverage set.",
                count=len(scope_leak_rows),
            )
        )

    finding_codes = sorted({item["code"] for item in findings})
    failed = bool(
        selected_coverage["exactMissing"]
        or selected_coverage["orphan"]
        or selected_coverage["duplicateDirectedKey"]
        or closed_selected["invalid"]
        or runtime_selected["invalid"]
        or scope_leak_rows
    )
    exit_code = 2 if failed else 0

    out_of_scope = [
        {
            "source": "runtime",
            "scope": _scope_as_dict(_row_scope(row)),
            "loopKey": _normalize_text(row.get("loop_key")),
            "state": _normalize_text(row.get("state")),
        }
        for row in sorted(out_of_scope_runtime, key=lambda item: (_row_scope(item), _normalize_text(item.get("loop_key"))))
    ]
    out_of_scope.extend(
        {
            "source": "closed-loop",
            "scope": _scope_as_dict(_row_scope(row)),
            "loopKey": _normalize_text(row.get("loop_key")),
            "state": _normalize_text(row.get("status")),
        }
        for row in sorted(out_of_scope_closed, key=lambda item: (_row_scope(item), _normalize_text(item.get("loop_key"))))
    )

    return {
        "schema": SCHEMA,
        "generatedAt": _utc_now(),
        "mode": "read-only",
        "scope": scope if not all_scopes else {"allScopes": True, **scope},
        "keyMode": key_mode,
        "databaseMetadata": metadata,
        "closedLoops": {
            "totalRows": len(closed_all),
            "rowsInScope": len(closed_scope),
            "outOfScopeRows": len(out_of_scope_closed),
            "scopes": _scope_summary(closed_all),
            "selectedKey": {
                "keyedRows": len(closed_selected["pairs"]),
                "invalidRows": len(closed_selected["invalid"]),
                "duplicates": closed_selected["duplicates"],
            },
            "directedSymbol": {
                "keyedRows": len(closed_directed["pairs"]),
                "invalidRows": len(closed_directed["invalid"]),
                "duplicates": closed_directed["duplicates"],
            },
            "yzGroup": {
                "keyedRows": len(closed_group["pairs"]),
                "invalidRows": len(closed_group["invalid"]),
                "duplicates": closed_group["duplicates"],
            },
            "physicalLoopKey": {
                "keyedRows": len(closed_physical["pairs"]),
                "invalidRows": len(closed_physical["invalid"]),
                "duplicates": closed_physical["duplicates"],
            },
        },
        "runtimeStates": {
            "totalRows": len(runtime_all),
            "rowsInScope": len(runtime_scope),
            "outOfScopeRows": len(out_of_scope_runtime),
            "scopes": _scope_summary(runtime_all),
            "stateCounts": dict(
                sorted(Counter(_normalize_text(row.get("state")) for row in runtime_scope).items())
            ),
            "selectedKey": {
                "keyedRows": len(runtime_selected["pairs"]),
                "invalidRows": len(runtime_selected["invalid"]),
                "duplicates": runtime_selected["duplicates"],
            },
            "directedSymbol": {
                "keyedRows": len(runtime_directed["pairs"]),
                "invalidRows": len(runtime_directed["invalid"]),
                "duplicates": runtime_directed["duplicates"],
            },
            "yzGroup": {
                "keyedRows": len(runtime_group["pairs"]),
                "invalidRows": len(runtime_group["invalid"]),
                "duplicates": runtime_group["duplicates"],
            },
            "physicalLoopKey": {
                "keyedRows": len(runtime_physical["pairs"]),
                "invalidRows": len(runtime_physical["invalid"]),
                "duplicates": runtime_physical["duplicates"],
            },
        },
        "coverage": coverage,
        "selectedCoverage": {
            "matchedKeys": selected_coverage["_matchedKeys"],
            "missingKeys": selected_coverage["_missingKeys"],
            "orphanKeys": selected_coverage["_orphanKeys"],
        },
        "diagnosticCoverage": {
            "directedSymbol": _public_coverage(directed_coverage),
            "yzGroup": _public_coverage(group_coverage),
            "physicalLoopKey": _public_coverage(physical_coverage),
        },
        "outOfScope": out_of_scope,
        "findings": sorted(findings, key=lambda item: item["code"]),
        "findingCodes": finding_codes,
        "exitCode": exit_code,
    }


def _base_report(
    *,
    store: Path,
    scope: dict[str, Any] | None,
    key_mode: str,
    error: str,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generatedAt": _utc_now(),
        "mode": "read-only",
        "scope": scope or {},
        "keyMode": key_mode,
        "databaseMetadata": {"path": str(store)},
        "closedLoops": {},
        "runtimeStates": {},
        "coverage": {},
        "outOfScope": [],
        "findings": [
            {
                "code": "AUDIT_CONTRACT_ERROR",
                "severity": "high",
                "summary": error,
            }
        ],
        "error": error,
        "exitCode": 3,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = ContractArgumentParser(
        description="Read-only closed-loop versus runtime identity audit."
    )
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--network", required=True)
    parser.add_argument("--chain-id", required=True, type=int)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--market-id", required=True)
    parser.add_argument("--key-mode", choices=KEY_MODES, default="loop-key")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--all-scopes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    scope: dict[str, Any] | None = None
    if args.output.exists():
        print(f"output already exists: {args.output}", file=sys.stderr)
        return 3
    try:
        scope = _canonical_scope(
            {
                "network": args.network,
                "chain_id": args.chain_id,
                "protocol": args.protocol,
                "market_id": args.market_id,
            }
        )
        report = _run_audit(
            store=args.store,
            scope=scope,
            key_mode=args.key_mode,
            all_scopes=args.all_scopes,
        )
    except AuditContractError as exc:
        report = _base_report(
            store=args.store,
            scope=scope,
            key_mode=args.key_mode,
            error=str(exc),
        )
    except Exception as exc:
        report = _base_report(
            store=args.store,
            scope=scope,
            key_mode=args.key_mode,
            error=f"{type(exc).__name__}: {exc}",
        )
        report["exitCode"] = 4

    try:
        _write_report(args.output, report)
    except OSError as exc:
        print(f"failed to write report: {exc}", file=sys.stderr)
        return 4

    coverage = report.get("coverage") or {}
    print(
        "wrote {path} exit={code} matched={matched} missing={missing} "
        "orphan={orphan}".format(
            path=args.output,
            code=report.get("exitCode"),
            matched=coverage.get("exactMatched", "-"),
            missing=coverage.get("exactMissing", "-"),
            orphan=coverage.get("orphan", "-"),
        )
    )
    return int(report.get("exitCode") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
