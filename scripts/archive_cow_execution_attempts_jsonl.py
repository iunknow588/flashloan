#!/usr/bin/env python3
"""Archive legacy cow execution attempt JSONL files after SQLite verification.

SQLite is the only online cow-record store.  This command moves the old
chain-scoped ``attempts.jsonl`` files into a timestamped archive directory so
they stop participating in online reads.  It refuses to run unless the official
SQLite store has at least one submission or review row.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "flashloan" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from core.db.database_path import resolve_database_path

DEFAULT_LOG_ROOT = (
    REPO_ROOT / "flashloan" / "src" / "runtime" / "logs" / "cow_execution_attempts"
)
DEFAULT_ARCHIVE_DIR = (
    REPO_ROOT / "flashloan" / "src" / "runtime" / "logs" / "cow_execution_attempts_archive"
)
DEFAULT_DATABASE = resolve_database_path()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def verify_sqlite_attempts(database: str | Path) -> dict:
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"official SQLite store not found: {path}")
    connection = sqlite3.connect(str(path))
    try:
        counts = {}
        for table in ("cow_execution_attempts", "cow_execution_reviews"):
            try:
                row = connection.execute(
                    f"SELECT COUNT(*) AS c FROM {table}"
                ).fetchone()
                counts[table] = int(row[0] or 0)
            except sqlite3.OperationalError:
                counts[table] = 0
    finally:
        connection.close()
    count = sum(counts.values())
    if count <= 0:
        raise ValueError(
            "refusing to archive JSONL when SQLite has no cow execution or review records; "
            "run the SQLite backfill first"
        )
    return {
        "database": str(path),
        "sqliteAttemptCount": count,
        "submissionRecordCount": counts["cow_execution_attempts"],
        "reviewRecordCount": counts["cow_execution_reviews"],
    }


def archive_cow_execution_attempts_jsonl(
    *,
    log_root: str | Path,
    archive_dir: str | Path,
    database: str | Path,
    apply: bool = False,
) -> dict:
    root = Path(log_root).resolve()
    target_root = Path(archive_dir).resolve()
    database = Path(database)
    verification = verify_sqlite_attempts(database)
    if not root.is_dir():
        raise FileNotFoundError(f"JSONL attempt log root not found: {root}")
    files = sorted(item for item in root.rglob("*") if item.is_file())
    if not files:
        return {
            **verification,
            "logRoot": str(root),
            "archiveDir": str(target_root),
            "movedFiles": 0,
            "applied": apply,
            "dryRun": not apply,
            "archivePath": None,
        }
    stamp = _stamp()
    archive_path = target_root / f"cow_execution_attempts_{stamp}"
    moved_files = []
    for source in files:
        relative = source.relative_to(root)
        destination = archive_path / relative
        moved_files.append(
            {
                "source": str(source),
                "destination": str(destination),
                "bytes": source.stat().st_size,
            }
        )
        if apply:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise FileExistsError(
                    f"archive destination already exists: {destination}"
                )
            shutil.move(str(source), str(destination))
    return {
        **verification,
        "logRoot": str(root),
        "archiveDir": str(target_root),
        "archivePath": str(archive_path) if apply else None,
        "movedFiles": len(moved_files),
        "applied": apply,
        "dryRun": not apply,
        "files": moved_files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-root", default=str(DEFAULT_LOG_ROOT))
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR))
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Move the files. Without this flag the command only verifies and prints the plan.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run alias. The command is a dry run unless --apply is given.",
    )
    args = parser.parse_args()
    result = archive_cow_execution_attempts_jsonl(
        log_root=args.log_root,
        archive_dir=args.archive_dir,
        database=args.database,
        apply=args.apply and not args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
