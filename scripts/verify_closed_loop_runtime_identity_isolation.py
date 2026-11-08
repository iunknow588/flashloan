#!/usr/bin/env python3
"""Rehearse closed-loop runtime identity migration on an isolated copy.

The verifier never opens the production store for writing. It copies a
P0-2.4 database snapshot, validates the dry-run migration report, exercises the
state-machine maintenance guards, archives legacy symbol rows, inserts one
physical runtime row per directed loop, and runs the read-only observation
script against the resulting copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "flashloan" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dex.runtime.closed_loop_identity import (  # noqa: E402
    normalize_physical_loop_key,
)
from dex.runtime.closed_loop_state_machine import (  # noqa: E402
    backfill_runtime_state_for_closed_loops,
    canonicalize_runtime_state_market_ids,
    prune_legacy_unresolved_runtime_state,
    prune_orphan_runtime_state,
    prune_test_residue_runtime_state,
)


SCHEMA = "closed-loop-runtime-identity-isolation-v1"
MIGRATION_SCHEMA = "closed-loop-runtime-identity-migration-v1"
OBSERVATION_SCHEMA = "closed-loop-runtime-watch-v2"
TASK = "P0-2.5"
CLOSED_TABLE = "v4_closed_loops"
RUNTIME_TABLE = "v4_closed_loop_runtime_state"
ARCHIVE_TABLE = "v4_closed_loop_runtime_state_archive"
TARGET_STATE = "offchain_pending"
ARCHIVE_REASON = "legacy_symbol_runtime_state_archived_before_rekey"
DEFAULT_PRODUCTION_STORE = REPO_ROOT / "flashloan" / "db" / "local_store_4663.sqlite3"
OBSERVATION_SCRIPT = REPO_ROOT / "scripts" / "observe_dex_closed_loops.py"

RUNTIME_ROW_COLUMNS = (
    "network",
    "chain_id",
    "protocol",
    "market_id",
    "loop_key",
    "state",
    "generation",
    "owner_thread",
    "tx_hash",
    "last_error",
    "updated_at",
    "review_cycle_id",
    "claimed_at",
    "heartbeat_at",
    "lease_until",
    "finished_at",
    "persisted_at",
)

ARCHIVE_DDL = f"""
CREATE TABLE IF NOT EXISTS {ARCHIVE_TABLE} (
    archived_at TEXT NOT NULL,
    archive_batch_id TEXT NOT NULL,
    archive_reason TEXT NOT NULL,
    source_report_checksum TEXT NOT NULL,
    network TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    protocol TEXT NOT NULL,
    market_id TEXT NOT NULL,
    loop_key TEXT NOT NULL,
    state TEXT NOT NULL,
    generation INTEGER NOT NULL,
    owner_thread TEXT,
    tx_hash TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    review_cycle_id TEXT,
    claimed_at TEXT,
    heartbeat_at TEXT,
    lease_until TEXT,
    finished_at TEXT,
    persisted_at TEXT,
    PRIMARY KEY (
        archive_batch_id, network, chain_id, protocol, market_id, loop_key
    )
)
"""


class ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(3, f"{self.prog}: error: {message}\n")


class IsolationContractError(RuntimeError):
    """The isolated rehearsal cannot proceed safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: Any, separator: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", separator, text)
    return re.sub(rf"{re.escape(separator)}+", separator, text).strip(separator)


def _canonical_scope(
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
) -> dict[str, Any]:
    try:
        parsed_chain_id = int(chain_id)
    except (TypeError, ValueError) as exc:
        raise IsolationContractError("chain-id must be a positive integer") from exc
    if parsed_chain_id <= 0:
        raise IsolationContractError("chain-id must be a positive integer")
    scope = {
        "network": _slug(network, "-"),
        "chainId": parsed_chain_id,
        "protocol": _slug(protocol, "_"),
        "marketId": _slug(market_id, "-"),
    }
    if not all(scope.values()):
        raise IsolationContractError(
            "network, chain-id, protocol, and market-id are required"
        )
    return scope


def _scope_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        _slug(left.get("network"), "-") == _slug(right.get("network"), "-")
        and int(left.get("chainId") or 0) == int(right.get("chainId") or 0)
        and _slug(left.get("protocol"), "_") == _slug(right.get("protocol"), "_")
        and _slug(left.get("marketId"), "-") == _slug(right.get("marketId"), "-")
    )


def _scope_params(scope: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(scope["network"]),
        int(scope["chainId"]),
        str(scope["protocol"]),
        str(scope["marketId"]),
    )


def _scope_where() -> str:
    return (
        "lower(network) = ? AND chain_id = ? AND lower(protocol) = ? "
        "AND replace(lower(market_id), '_', '-') = ?"
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_digest(rows: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        payload = json.dumps(
            {key: row[key] for key in sorted(row)},
            ensure_ascii=True,
            separators=(",", ":"),
            default=str,
        )
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _open_read_transaction(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _require_schema(connection: sqlite3.Connection) -> None:
    missing_tables = [
        table
        for table in (CLOSED_TABLE, RUNTIME_TABLE)
        if not _table_exists(connection, table)
    ]
    if missing_tables:
        raise IsolationContractError(
            f"required tables are missing: {', '.join(missing_tables)}"
        )
    for table in (CLOSED_TABLE, RUNTIME_TABLE):
        missing_columns = sorted(
            {
                "network",
                "chain_id",
                "protocol",
                "market_id",
                "loop_key",
            }
            - _table_columns(connection, table)
        )
        if missing_columns:
            raise IsolationContractError(
                f"required columns are missing from {table}: "
                f"{', '.join(missing_columns)}"
            )
    runtime_missing = sorted(
        set(RUNTIME_ROW_COLUMNS) - _table_columns(connection, RUNTIME_TABLE)
    )
    if runtime_missing:
        raise IsolationContractError(
            "runtime schema cannot be archived safely; missing columns: "
            f"{', '.join(runtime_missing)}"
        )


def _quick_check(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute("PRAGMA quick_check").fetchall()
    finally:
        connection.close()
    values = [str(row[0]) for row in rows]
    return "ok" if values == ["ok"] else "; ".join(values)


def _checkpoint_database(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        busy, log_frames, checkpointed_frames = connection.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)"
        ).fetchone()
    finally:
        connection.close()
    result = {
        "busy": int(busy),
        "logFrames": int(log_frames),
        "checkpointedFrames": int(checkpointed_frames),
    }
    if result["busy"] != 0:
        raise IsolationContractError("copy DB WAL checkpoint could not complete")
    return result


def _read_rows(
    connection: sqlite3.Connection,
    table: str,
    scope: dict[str, Any],
) -> list[dict[str, Any]]:
    cursor = connection.execute(
        f"SELECT * FROM {table} WHERE {_scope_where()} ORDER BY loop_key",
        _scope_params(scope),
    )
    columns = [str(column[0]) for column in cursor.description or ()]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _summarize_runtime_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    physical = 0
    legacy = 0
    states: Counter[str] = Counter()
    for row in rows:
        if normalize_physical_loop_key(row.get("loop_key")):
            physical += 1
        else:
            legacy += 1
        states[str(row.get("state") or "")] += 1
    return {
        "total": len(rows),
        "physicalDirectionKeys": physical,
        "legacySymbolOrOtherKeys": legacy,
        "states": dict(sorted(states.items())),
        "rowChecksum": _json_digest(rows),
    }


def _summarize_database(path: Path, scope: dict[str, Any]) -> dict[str, Any]:
    connection = _open_read_transaction(path)
    try:
        _require_schema(connection)
        closed_rows = _read_rows(connection, CLOSED_TABLE, scope)
        runtime_rows = _read_rows(connection, RUNTIME_TABLE, scope)
        closed_keys = {
            str(row.get("loop_key") or "").strip().lower() for row in closed_rows
        }
        connection.execute("COMMIT")
    finally:
        connection.close()
    return {
        "closedLoops": len(closed_rows),
        "closedLoopKeys": closed_keys,
        "closedRowsChecksum": _json_digest(closed_rows),
        "runtime": _summarize_runtime_rows(runtime_rows),
    }


def _resolve_report_path(raw_path: Any, report_path: Path) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise IsolationContractError("migration source report path is empty")
    candidate = Path(text)
    candidates = [candidate]
    if not candidate.is_absolute():
        candidates.extend((REPO_ROOT / candidate, report_path.parent / candidate))
    for item in candidates:
        if item.is_file():
            return item.resolve()
    raise IsolationContractError(f"migration source report does not exist: {text}")


def _load_migration_report(
    *,
    path: Path,
    source_store_sha256: str,
    scope: dict[str, Any],
    expected_closed_loops: int,
) -> tuple[dict[str, Any], str]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise IsolationContractError(f"invalid migration report: {exc}") from exc
    if report.get("schema") != MIGRATION_SCHEMA:
        raise IsolationContractError("unexpected migration report schema")
    if report.get("task") != "P0-2.4" or report.get("mode") != "dry-run":
        raise IsolationContractError("migration report is not a P0-2.4 dry-run")
    if int(report.get("exitCode") or 0) != 0:
        raise IsolationContractError("P0-2.4 dry-run did not exit successfully")
    if not _scope_equal(report.get("scope") or {}, scope):
        raise IsolationContractError("migration report scope does not match")
    database_metadata = report.get("databaseMetadata") or {}
    if str(database_metadata.get("sha256") or "").lower() != source_store_sha256:
        raise IsolationContractError(
            "migration report database SHA256 does not match the source store"
        )
    source = report.get("source") or {}
    source_report_path = _resolve_report_path(source.get("reportPath"), path)
    source_report_sha256 = _file_sha256(source_report_path)
    if source_report_sha256 != str(source.get("reportChecksum") or "").lower():
        raise IsolationContractError("source report checksum does not match")
    counts = report.get("counts") or {}
    expected_counts = {
        "closedLoops": expected_closed_loops,
        "proposedDirectionMappings": expected_closed_loops,
        "proposedRuntimeStates": expected_closed_loops,
        "archivePlans": expected_closed_loops // 2,
        "rekeyPlans": expected_closed_loops // 2,
        "reverseInsertPlans": expected_closed_loops // 2,
        "unresolvedDirections": 0,
        "duplicateDirectedKey": 0,
        "unresolvedAddress": 0,
        "crossScopeConflict": 0,
    }
    mismatches = [
        key for key, expected in expected_counts.items() if int(counts.get(key) or 0) != expected
    ]
    if mismatches:
        raise IsolationContractError(
            f"migration report counts do not match: {', '.join(mismatches)}"
        )
    if report.get("findingCodes") not in ([], ["OUT_OF_SCOPE_ROWS_EXCLUDED"]):
        raise IsolationContractError("migration report has blocking finding codes")
    return report, _file_sha256(path)


def _validate_plan_against_database(
    report: dict[str, Any],
    database_summary: dict[str, Any],
    scope: dict[str, Any],
) -> None:
    archive_plans = list(report.get("archivePlans") or [])
    proposed_states = list(report.get("proposedRuntimeStates") or [])
    source_keys = {
        str(plan.get("sourceRuntimeKey") or "").strip().lower()
        for plan in archive_plans
    }
    if len(source_keys) != len(archive_plans):
        raise IsolationContractError("archive plans contain duplicate source keys")

    connection_rows = database_summary.get("runtimeRows") or []
    database_source_keys = {
        str(row.get("loop_key") or "").strip().lower()
        for row in connection_rows
        if not normalize_physical_loop_key(row.get("loop_key"))
    }
    if source_keys != database_source_keys:
        raise IsolationContractError(
            "migration archive plan does not cover the copy DB legacy rows"
        )

    closed_keys = set(database_summary.get("closedLoopKeys") or ())
    target_keys: set[str] = set()
    for row in proposed_states:
        target = normalize_physical_loop_key(row.get("loop_key"))
        if target is None:
            raise IsolationContractError("proposed runtime key is not physical")
        if target not in closed_keys:
            raise IsolationContractError(
                f"proposed runtime key has no closed loop: {target}"
            )
        if target in target_keys:
            raise IsolationContractError("proposed runtime keys contain duplicates")
        target_keys.add(target)
        if str(row.get("state") or "") != TARGET_STATE:
            raise IsolationContractError("proposed runtime state is not offchain_pending")
        if not _scope_equal(
            {
                "network": row.get("network"),
                "chainId": row.get("chain_id"),
                "protocol": row.get("protocol"),
                "marketId": row.get("market_id"),
            },
            scope,
        ):
            raise IsolationContractError("proposed runtime row escaped the scope")
    if target_keys != closed_keys:
        raise IsolationContractError(
            "proposed runtime keys do not exactly cover directed closed loops"
        )


def _ensure_paths_are_isolated(
    *,
    source_store: Path,
    working_store: Path,
    production_store: Path,
) -> None:
    source = source_store.resolve()
    working = working_store.resolve()
    production = production_store.resolve()
    if source == production:
        raise IsolationContractError("source store must not be the production store")
    if working == production:
        raise IsolationContractError("working store must not be the production store")
    if source == working:
        raise IsolationContractError("working store must be a new copy")


def _copy_source_store(source_store: Path, working_store: Path) -> str:
    if not source_store.is_file():
        raise IsolationContractError(f"source store does not exist: {source_store}")
    if working_store.exists():
        raise IsolationContractError(
            f"working store already exists and will not be overwritten: {working_store}"
        )
    working_store.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_store, working_store)
    copied_sha256 = _file_sha256(working_store)
    if copied_sha256 != _file_sha256(source_store):
        raise IsolationContractError("copied store checksum does not match source")
    return copied_sha256


def _test_residue_scope(scope: dict[str, Any]) -> dict[str, Any]:
    if int(scope["chainId"]) != 4663:
        return {
            "network": "robinhood-testnet",
            "chainId": 46630,
            "protocol": "uniswap_v4",
            "marketId": "dex-arbitrage-46630-uniswap-v4",
        }
    return {
        "network": "robinhood-testnet",
        "chainId": 46630,
        "protocol": str(scope["protocol"]),
        "marketId": "dex-arbitrage-46630-uniswap-v4",
    }


def _maintenance_args(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "network": str(scope["network"]),
        "chain_id": int(scope["chainId"]),
        "protocol": str(scope["protocol"]),
        "market_id": str(scope["marketId"]),
    }


def _run_guard_checks(
    working_store: Path,
    *,
    scope: dict[str, Any],
    testnet_scope: dict[str, Any],
    expected_legacy_rows: int,
) -> dict[str, Any]:
    main_args = _maintenance_args(scope)
    testnet_args = _maintenance_args(testnet_scope)
    testnet_before = _summarize_database(working_store, testnet_scope)["runtime"]

    unresolved = prune_legacy_unresolved_runtime_state(working_store, **main_args)
    legacy_prune = prune_orphan_runtime_state(working_store, **main_args)
    residue_prune = prune_test_residue_runtime_state(working_store, **testnet_args)
    testnet_orphan_prune = prune_orphan_runtime_state(working_store, **testnet_args)

    approval_gate_enforced = False
    try:
        prune_test_residue_runtime_state(
            working_store,
            apply_cleanup=True,
            **testnet_args,
        )
    except ValueError:
        approval_gate_enforced = True

    testnet_after = _summarize_database(working_store, testnet_scope)["runtime"]
    if (
        int(legacy_prune.get("legacySymbolIdentityPreservedCount") or 0)
        != expected_legacy_rows
    ):
        raise IsolationContractError(
            "ordinary maintenance did not preserve the legacy symbol states"
        )
    if int(legacy_prune.get("deletedCount") or 0) != 0:
        raise IsolationContractError("ordinary maintenance deleted copy DB runtime rows")
    if int(residue_prune.get("residueDetected") or 0) != 1:
        raise IsolationContractError("known Testnet residue was not detected")
    if int(residue_prune.get("residualPruned") or 0) != 0:
        raise IsolationContractError("known Testnet residue was pruned without approval")
    if int(testnet_orphan_prune.get("protectedTestResidueCount") or 0) != 1:
        raise IsolationContractError("orphan maintenance did not protect Testnet residue")
    if testnet_before["rowChecksum"] != testnet_after["rowChecksum"]:
        raise IsolationContractError("Testnet residue changed during guard checks")
    if not approval_gate_enforced:
        raise IsolationContractError("explicit cleanup approval gate is not enforced")
    return {
        "legacyUnresolvedPrune": unresolved,
        "legacyIdentityPrune": legacy_prune,
        "testnetResiduePreserve": residue_prune,
        "testnetOrphanPreserve": testnet_orphan_prune,
        "approvalGateEnforced": approval_gate_enforced,
        "testnetBefore": testnet_before,
        "testnetAfter": testnet_after,
    }


def _apply_rehearsal_plan(
    working_store: Path,
    *,
    report: dict[str, Any],
    scope: dict[str, Any],
    migration_report_checksum: str,
) -> dict[str, Any]:
    archive_plans = list(report.get("archivePlans") or [])
    proposed_states = list(report.get("proposedRuntimeStates") or [])
    source_report_checksum = str(
        (report.get("source") or {}).get("reportChecksum") or ""
    )
    archive_batch_id = (
        "p0-2-5:" + hashlib.sha256(migration_report_checksum.encode("ascii")).hexdigest()[:16]
    )
    applied_at = _utc_now()
    archived_at = applied_at
    connection = sqlite3.connect(working_store, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        _require_schema(connection)
        connection.execute(ARCHIVE_DDL)
        archive_exists = connection.execute(
            f"SELECT COUNT(*) FROM {ARCHIVE_TABLE} WHERE archive_batch_id = ?",
            (archive_batch_id,),
        ).fetchone()[0]
        if int(archive_exists or 0) != 0:
            raise IsolationContractError("archive batch already exists")

        target_keys = [
            normalize_physical_loop_key(row.get("loop_key"))
            for row in proposed_states
        ]
        placeholders = ",".join("?" for _ in target_keys)
        existing_targets = connection.execute(
            f"""
            SELECT loop_key FROM {RUNTIME_TABLE}
            WHERE {_scope_where()} AND lower(loop_key) IN ({placeholders})
            """,
            (*_scope_params(scope), *target_keys),
        ).fetchall()
        if existing_targets:
            raise IsolationContractError(
                "copy DB already has physical target runtime rows"
            )

        connection.execute("BEGIN IMMEDIATE")
        archived = 0
        deleted = 0
        for plan in archive_plans:
            source_key = str(plan.get("sourceRuntimeKey") or "").strip().lower()
            cursor = connection.execute(
                f"""
                INSERT INTO {ARCHIVE_TABLE} (
                    archived_at, archive_batch_id, archive_reason,
                    source_report_checksum, {", ".join(RUNTIME_ROW_COLUMNS)}
                )
                SELECT ?, ?, ?, ?, {", ".join(RUNTIME_ROW_COLUMNS)}
                FROM {RUNTIME_TABLE}
                WHERE {_scope_where()} AND lower(loop_key) = ?
                """,
                (
                    archived_at,
                    archive_batch_id,
                    ARCHIVE_REASON,
                    source_report_checksum,
                    *_scope_params(scope),
                    source_key,
                ),
            )
            archived += int(cursor.rowcount or 0)
            cursor = connection.execute(
                f"""
                DELETE FROM {RUNTIME_TABLE}
                WHERE {_scope_where()} AND lower(loop_key) = ?
                """,
                (*_scope_params(scope), source_key),
            )
            deleted += int(cursor.rowcount or 0)

        inserted = 0
        updated_at_index = RUNTIME_ROW_COLUMNS.index("updated_at")
        for row in proposed_states:
            values = [row.get(column) for column in RUNTIME_ROW_COLUMNS]
            if not values[updated_at_index]:
                values[updated_at_index] = applied_at
            connection.execute(
                f"""
                INSERT INTO {RUNTIME_TABLE} ({", ".join(RUNTIME_ROW_COLUMNS)})
                VALUES ({", ".join("?" for _ in RUNTIME_ROW_COLUMNS)})
                """,
                tuple(values),
            )
            inserted += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    if archived != len(archive_plans):
        raise IsolationContractError("archive row count does not match plan")
    if deleted != len(archive_plans):
        raise IsolationContractError("legacy runtime delete count does not match plan")
    if inserted != len(proposed_states):
        raise IsolationContractError("physical runtime insert count does not match plan")
    return {
        "archiveBatchId": archive_batch_id,
        "archivedRows": archived,
        "deletedLegacyRows": deleted,
        "insertedPhysicalRows": inserted,
    }


def _run_post_migration_maintenance(
    working_store: Path,
    *,
    scope: dict[str, Any],
    expected_rows: int,
) -> dict[str, Any]:
    args = _maintenance_args(scope)
    canonicalize = canonicalize_runtime_state_market_ids(working_store, **args)
    orphan_prune = prune_orphan_runtime_state(working_store, **args)
    backfill = backfill_runtime_state_for_closed_loops(working_store, **args)
    if int(canonicalize.get("conflicts") or 0) != 0:
        raise IsolationContractError("runtime market-id canonicalization conflicted")
    if int(orphan_prune.get("deletedCount") or 0) != 0:
        raise IsolationContractError("post-migration maintenance deleted runtime rows")
    if int(orphan_prune.get("matchedClosedLoopCount") or 0) != expected_rows:
        raise IsolationContractError("post-migration physical identity did not match")
    if int(backfill.get("created") or 0) != 0:
        raise IsolationContractError("post-migration backfill was not idempotent")
    if int(backfill.get("existing") or 0) != expected_rows:
        raise IsolationContractError("post-migration backfill coverage mismatch")
    return {
        "canonicalize": canonicalize,
        "orphanPrune": orphan_prune,
        "backfill": backfill,
    }


def _parse_observation(path: Path, scope: dict[str, Any], expected_rows: int) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise IsolationContractError(f"cannot read observation output: {exc}") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            sample = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IsolationContractError("observation output contains invalid JSON") from exc
        if sample.get("schema") != OBSERVATION_SCHEMA:
            raise IsolationContractError("unexpected observation schema")
        if not _scope_equal(sample.get("scope") or {}, scope):
            raise IsolationContractError("observation scope drifted")
        if sample.get("scopeApplied") is not True:
            raise IsolationContractError("observation did not enforce scope")
        if sample.get("runtimeStates") != {TARGET_STATE: expected_rows}:
            raise IsolationContractError("observation runtime state coverage mismatch")
        samples.append(sample)
    if len(samples) < 2:
        raise IsolationContractError("observation produced fewer than two samples")
    if samples[-1].get("windowComplete") is not True:
        raise IsolationContractError("observation window did not complete")
    testnet_runtime = [
        row
        for sample in samples
        for row in (sample.get("outOfScope") or {}).get("rows", [])
        if row.get("table") == RUNTIME_TABLE
        and int(row.get("chainId") or 0) == 46630
        and int(row.get("count") or 0) == 1
    ]
    if not testnet_runtime:
        raise IsolationContractError(
            "observation did not report the protected Testnet residue as out of scope"
        )
    return {
        "sampleCount": len(samples),
        "firstSampleAt": samples[0].get("utc"),
        "lastSampleAt": samples[-1].get("utc"),
        "runtimeStates": samples[-1].get("runtimeStates"),
        "outOfScopeTotal": samples[-1].get("outOfScope", {}).get("total"),
        "windowComplete": True,
    }


def _run_observation(
    *,
    working_store: Path,
    scope: dict[str, Any],
    output_path: Path,
    duration_seconds: int,
    interval_seconds: int,
    expected_rows: int,
) -> dict[str, Any]:
    if output_path.exists():
        raise IsolationContractError(
            f"observation output already exists: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(OBSERVATION_SCRIPT),
        "--database",
        str(working_store),
        "--network",
        str(scope["network"]),
        "--chain-id",
        str(scope["chainId"]),
        "--protocol",
        str(scope["protocol"]),
        "--market-id",
        str(scope["marketId"]),
        "--out",
        str(output_path),
        "--duration-seconds",
        str(duration_seconds),
        "--interval-seconds",
        str(interval_seconds),
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=max(30, duration_seconds + 30),
        check=False,
    )
    if completed.returncode != 0:
        raise IsolationContractError(
            "observation script failed: "
            f"exit={completed.returncode} stderr={completed.stderr.strip()}"
        )
    summary = _parse_observation(output_path, scope, expected_rows)
    return {
        "command": command,
        "exitCode": completed.returncode,
        "stdoutTail": completed.stdout[-2000:],
        "stderrTail": completed.stderr[-2000:],
        **summary,
    }


def _verify(args: argparse.Namespace) -> dict[str, Any]:
    scope = _canonical_scope(
        network=args.network,
        chain_id=args.chain_id,
        protocol=args.protocol,
        market_id=args.market_id,
    )
    if args.expected_closed_loops <= 0 or args.expected_closed_loops % 2:
        raise IsolationContractError(
            "expected-closed-loops must be a positive even integer"
        )
    source_store = args.source_store.resolve()
    working_store = args.working_store.resolve()
    production_store = args.production_store.resolve()
    _ensure_paths_are_isolated(
        source_store=source_store,
        working_store=working_store,
        production_store=production_store,
    )
    if not source_store.is_file():
        raise IsolationContractError(f"source store does not exist: {source_store}")

    source_sha256 = _file_sha256(source_store)
    report, migration_report_checksum = _load_migration_report(
        path=args.migration_report.resolve(),
        source_store_sha256=source_sha256,
        scope=scope,
        expected_closed_loops=args.expected_closed_loops,
    )
    copied_sha256 = _copy_source_store(source_store, working_store)
    before = _summarize_database(working_store, scope)
    if before["closedLoops"] != args.expected_closed_loops:
        raise IsolationContractError("copy DB closed-loop count mismatch")
    if before["runtime"]["legacySymbolOrOtherKeys"] != args.expected_closed_loops // 2:
        raise IsolationContractError("copy DB legacy runtime count mismatch")

    source_connection = _open_read_transaction(working_store)
    try:
        before["runtimeRows"] = _read_rows(
            source_connection,
            RUNTIME_TABLE,
            scope,
        )
    finally:
        source_connection.rollback()
        source_connection.close()
    _validate_plan_against_database(report, before, scope)
    before.pop("runtimeRows", None)
    before.pop("closedLoopKeys", None)

    testnet_scope = _test_residue_scope(scope)
    guard_checks = _run_guard_checks(
        working_store,
        scope=scope,
        testnet_scope=testnet_scope,
        expected_legacy_rows=args.expected_closed_loops // 2,
    )
    rehearsal = _apply_rehearsal_plan(
        working_store,
        report=report,
        scope=scope,
        migration_report_checksum=migration_report_checksum,
    )
    post_maintenance = _run_post_migration_maintenance(
        working_store,
        scope=scope,
        expected_rows=args.expected_closed_loops,
    )
    testnet_after_migration = _summarize_database(working_store, testnet_scope)["runtime"]
    if guard_checks["testnetBefore"]["rowChecksum"] != testnet_after_migration["rowChecksum"]:
        raise IsolationContractError("Testnet residue changed during migration rehearsal")

    after = _summarize_database(working_store, scope)
    after.pop("closedLoopKeys", None)
    if after["closedLoops"] != before["closedLoops"]:
        raise IsolationContractError("closed-loop count changed during rehearsal")
    if after["closedRowsChecksum"] != before["closedRowsChecksum"]:
        raise IsolationContractError("closed-loop contents changed during rehearsal")
    if int(after["runtime"]["total"]) != args.expected_closed_loops:
        raise IsolationContractError("runtime row count is not directed coverage")
    if int(after["runtime"]["physicalDirectionKeys"]) != args.expected_closed_loops:
        raise IsolationContractError("runtime rows are not all physical direction keys")
    if int(after["runtime"]["legacySymbolOrOtherKeys"]) != 0:
        raise IsolationContractError("legacy symbol rows remain in active runtime table")
    if after["runtime"]["states"] != {TARGET_STATE: args.expected_closed_loops}:
        raise IsolationContractError("post-migration state distribution mismatch")

    connection = _open_read_transaction(working_store)
    try:
        cursor = connection.execute(
            f"""
            SELECT * FROM {ARCHIVE_TABLE}
            WHERE archive_batch_id = ?
            ORDER BY loop_key
            """,
            (rehearsal["archiveBatchId"],),
        )
        archive_columns = [str(column[0]) for column in cursor.description or ()]
        archive_rows = [dict(zip(archive_columns, row)) for row in cursor.fetchall()]
        connection.execute("COMMIT")
    finally:
        connection.close()
    if len(archive_rows) != args.expected_closed_loops // 2:
        raise IsolationContractError("archive table row count mismatch")
    archive_states = Counter(str(row.get("state") or "") for row in archive_rows)
    if not archive_states:
        raise IsolationContractError("archive table is empty")

    observation = _run_observation(
        working_store=working_store,
        scope=scope,
        output_path=args.observation_output.resolve(),
        duration_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
        expected_rows=args.expected_closed_loops,
    )
    checkpoint = _checkpoint_database(working_store)
    final_sha256 = _file_sha256(working_store)
    if _file_sha256(source_store) != source_sha256:
        raise IsolationContractError("source snapshot changed during verification")
    quick_check = _quick_check(working_store)
    if quick_check != "ok":
        raise IsolationContractError(f"copy DB quick_check failed: {quick_check}")
    if final_sha256 == copied_sha256:
        raise IsolationContractError("copy DB checksum did not change after rehearsal")

    return {
        "schema": SCHEMA,
        "generatedAt": _utc_now(),
        "task": TASK,
        "evidenceLevel": "isolated_copy_db_and_read_only_observation",
        "conclusion": "passed",
        "scope": scope,
        "authority": "legacy_python_closed_loop_runtime_identity",
        "expectedClosedLoops": args.expected_closed_loops,
        "sourceStore": {
            "path": str(source_store),
            "sha256": source_sha256,
            "unchanged": True,
        },
        "workingStore": {
            "path": str(working_store),
            "copiedSha256": copied_sha256,
            "finalSha256": final_sha256,
            "quickCheck": quick_check,
            "walCheckpoint": checkpoint,
            "checksumChanged": True,
            "changeReason": "legacy runtime rows archived and 192 physical runtime rows inserted",
        },
        "migrationReport": {
            "path": str(args.migration_report.resolve()),
            "sha256": migration_report_checksum,
            "sourceReportChecksum": (report.get("source") or {}).get("reportChecksum"),
        },
        "before": before,
        "after": after,
        "guardChecks": guard_checks,
        "rehearsal": rehearsal,
        "postMigrationMaintenance": post_maintenance,
        "archive": {
            "table": ARCHIVE_TABLE,
            "rows": len(archive_rows),
            "states": dict(sorted(archive_states.items())),
        },
        "testnetPreservation": {
            "scope": testnet_scope,
            "rowChecksumBefore": guard_checks["testnetBefore"]["rowChecksum"],
            "rowChecksumAfterMigration": testnet_after_migration["rowChecksum"],
            "unchanged": True,
        },
        "observation": observation,
        "assertions": {
            "legacySymbolRowsArchivedNotDeleted": True,
            "directionRowCountExact": True,
            "allDirectionStatesOffchainPending": True,
            "closedLoopRowsUnchanged": True,
            "testnetResidueProtectedByDefault": True,
            "explicitCleanupApprovalGateEnforced": True,
            "observationScopeEnforced": True,
        },
        "productionBoundary": {
            "productionStorePath": str(production_store),
            "productionStoreAccessed": False,
            "productionStoreWritten": False,
            "productionServiceRestarted": False,
            "productionWorkerExecuted": False,
            "note": "This is isolated copy DB evidence, not production apply.",
        },
        "exitCode": 0,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def _error_report(
    args: argparse.Namespace,
    *,
    error: str,
    exit_code: int,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generatedAt": _utc_now(),
        "task": TASK,
        "evidenceLevel": "isolated_copy_db_and_read_only_observation",
        "conclusion": "failed",
        "scope": {
            "network": _slug(args.network, "-"),
            "chainId": int(args.chain_id),
            "protocol": _slug(args.protocol, "_"),
            "marketId": _slug(args.market_id, "-"),
        },
        "sourceStore": {"path": str(args.source_store.resolve())},
        "workingStore": {"path": str(args.working_store.resolve())},
        "observationOutput": str(args.observation_output.resolve()),
        "error": error,
        "productionBoundary": {
            "productionStorePath": str(args.production_store.resolve()),
            "productionStoreAccessed": False,
            "productionStoreWritten": False,
            "productionServiceRestarted": False,
        },
        "exitCode": exit_code,
    }


def _parser() -> argparse.ArgumentParser:
    parser = ContractArgumentParser(
        description="Verify closed-loop identity migration on an isolated copy."
    )
    parser.add_argument("--source-store", required=True, type=Path)
    parser.add_argument("--working-store", required=True, type=Path)
    parser.add_argument("--migration-report", required=True, type=Path)
    parser.add_argument("--network", required=True)
    parser.add_argument("--chain-id", required=True, type=int)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--market-id", required=True)
    parser.add_argument("--production-store", type=Path, default=DEFAULT_PRODUCTION_STORE)
    parser.add_argument("--expected-closed-loops", type=int, default=192)
    parser.add_argument("--duration-seconds", type=int, default=3)
    parser.add_argument("--interval-seconds", type=int, default=1)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--observation-output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.output.exists():
        print(f"output already exists: {args.output}", file=sys.stderr)
        return 3
    if args.observation_output.exists():
        print(
            f"observation output already exists: {args.observation_output}",
            file=sys.stderr,
        )
        return 3
    try:
        report = _verify(args)
        exit_code = 0
    except IsolationContractError as exc:
        report = _error_report(args, error=str(exc), exit_code=2)
        exit_code = 2
    except Exception as exc:
        report = _error_report(
            args,
            error=f"{type(exc).__name__}: {exc}",
            exit_code=4,
        )
        exit_code = 4
    _write_report(args.output, report)
    print(json.dumps({"exitCode": exit_code, "output": str(args.output)}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
